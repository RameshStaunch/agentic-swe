import asyncio
import io
import shutil
import socket
from pathlib import Path

import pytest
from rich.console import Console

from agentic_swe.models import (CodeChange, DesignDoc, EngineeringSummary, FileWrite, NormalizedRequirement, Task,
                                TaskGraph, ValidationReport)
from agentic_swe.orchestrator import Gate, Orchestrator, validate_graph, waves

ROOT = Path(__file__).parent.parent


def t(id, deps=(), files=None):
    return Task(id=id, title=id, kind="codegen", description=id, depends_on=list(deps), files=files or [f"{id}.py"])


def quiet() -> Console:
    return Console(file=io.StringIO(), record=True)


def never_ask(q: str) -> str:
    raise AssertionError(f"unexpected prompt: {q}")


# ---------------------------------------------------------------- plan validation

def test_waves_layers_by_dependency():
    g = TaskGraph(rationale="", tasks=[t("a"), t("b"), t("c", ["a", "b"]), t("d", ["c"])])
    assert [[x.id for x in w] for w in waves(g)] == [["a", "b"], ["c"], ["d"]]
    assert validate_graph(g) == []


def test_rejects_unknown_dependency_and_cycles():
    assert "a depends on unknown task zz" in validate_graph(TaskGraph(rationale="", tasks=[t("a", ["zz"])]))
    assert validate_graph(TaskGraph(rationale="", tasks=[t("a", ["b"]), t("b", ["a"])])) == ["dependency cycle"]


def test_rejects_parallel_tasks_sharing_files():
    g = TaskGraph(rationale="", tasks=[t("a", files=["x.py"]), t("b", files=["x.py"])])
    assert any("can run in parallel" in e for e in validate_graph(g))
    ordered = TaskGraph(rationale="", tasks=[t("a", files=["x.py"]), t("b", ["a"], files=["x.py"])])
    assert validate_graph(ordered) == []


# ---------------------------------------------------------------- guardrails

def test_full_auto_blocks_escapes_protected_and_out_of_scope(tmp_path):
    gate = Gate("full-auto", never_ask, quiet())
    writes = [FileWrite(path=p, content="x") for p in ["a.py", "../evil.py", ".git/config", "other.py"]]
    allowed, feedback = asyncio.run(gate.review_writes(t("a", files=["a.py"]), tmp_path, writes))
    assert [w.path for w in allowed] == ["a.py"] and feedback is None


def test_suggest_mode_turns_free_text_into_feedback(tmp_path):
    gate = Gate("suggest", lambda q: "use a dataclass instead", quiet())
    allowed, feedback = asyncio.run(gate.review_writes(t("a"), tmp_path, [FileWrite(path="a.py", content="x")]))
    assert allowed == [] and feedback == "use a dataclass instead"


def test_auto_edit_asks_only_for_out_of_scope(tmp_path):
    asked = []
    gate = Gate("auto-edit", lambda q: asked.append(q) or "n", quiet())
    allowed, _ = asyncio.run(gate.review_writes(t("a"), tmp_path, [FileWrite(path="a.py", content="x"), FileWrite(path="b.py", content="x")]))
    assert [w.path for w in allowed] == ["a.py"] and len(asked) == 1


def test_full_auto_uses_assumptions_for_ambiguities():
    req = NormalizedRequirement(intent="i", scope="brownfield", ambiguities=["which?"], clarifying_questions=["which one?"],
                                assumptions=["the list endpoint"], acceptance_criteria=[])
    assert asyncio.run(Gate("full-auto", never_ask, quiet()).clarify(req)) == ["(no answer, assumed) the list endpoint"]


# ---------------------------------------------------------------- orchestration: recovery and failure propagation

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from mathy import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"


def write_replay(d: Path, steps: dict):
    (d / "steps").mkdir(parents=True)
    for name, m in steps.items():
        (d / "steps" / f"{name}.json").write_text(m.model_dump_json())


def base_steps(tasks):
    return {
        "1-analyst": NormalizedRequirement(intent="add numbers", scope="greenfield", ambiguities=[], clarifying_questions=[],
                                           assumptions=[], acceptance_criteria=["add works"]),
        "2-architect": DesignDoc(components=["mathy"], api_contract=[], data_model="-", key_decisions=[]),
        "3-decomposer": TaskGraph(rationale="r", tasks=tasks),
        "6-validator": ValidationReport(recommendation="approve", risks=[], trade_offs=[], failure_scenarios=[], guardrails=[]),
        "7-summary": EngineeringSummary(plan="p", rationale="r", risks=[], trade_offs=[], assumptions=[], limitations=[]),
    }


def run_replay(tmp_path, steps):
    replay, repo, events = tmp_path / "replay", tmp_path / "repo", []
    write_replay(replay, steps)

    async def record(kind, payload):
        events.append((kind, payload))

    orch = Orchestrator(repo, None, Gate("full-auto", never_ask, quiet()), record, tmp_path / "run", quiet(), replay)  # analyst decides scope
    res = asyncio.run(orch.run("add two numbers"))
    assert orch.scope == "greenfield"
    return res, events, repo


def test_failing_tests_trigger_a_fix_task(tmp_path):
    tasks = [t("code", files=["mathy.py"]), Task(id="tests", title="t", kind="test", description="t", depends_on=["code"], files=["test_mathy.py"])]
    steps = base_steps(tasks) | {
        "4-task-code": CodeChange(files=[FileWrite(path="mathy.py", content=BUGGY)], notes=""),
        "4-task-tests": CodeChange(files=[FileWrite(path="test_mathy.py", content=TEST)], notes=""),
        "5-fix-1": CodeChange(files=[FileWrite(path="mathy.py", content=FIXED)], notes="sign error"),
    }
    res, events, repo = run_replay(tmp_path, steps)
    kinds = [k for k, _ in events]
    assert res.tests_passed and res.status == "ready_for_review"
    assert kinds.count("tests") == 2 and "fix_attempt" in kinds
    assert (repo / "mathy.py").read_text() == FIXED
    assert "+    return a + b" in (tmp_path / "run" / "changes.patch").read_text()


def test_failed_task_blocks_dependents(tmp_path):
    tasks = [t("code", files=["mathy.py"]), Task(id="tests", title="t", kind="test", description="t", depends_on=["code"], files=["test_mathy.py"])]
    res, events, _ = run_replay(tmp_path, base_steps(tasks))  # no 4-task-code step recorded -> the coder "fails"
    assert res.task_status == {"code": "failed", "tests": "blocked"}
    assert sum(k == "task_error" and p["task"] == "code" for k, p in events) == 2  # original attempt + one retry
    assert res.status == "needs_review"


# ---------------------------------------------------------------- golden replays of the committed examples

def postgres_up() -> bool:
    with socket.socket() as s:
        return s.connect_ex(("localhost", 55432)) == 0


@pytest.mark.parametrize("example,requirement,scope,seed", [
    ("brownfield-pagination", "Add pagination to the notes list endpoint", "brownfield", "seed_repo"),
    ("ambiguous-make-it-faster", "Make the notes API faster", "brownfield", "seed_repo"),
    ("greenfield-url-shortener", "Build a scalable URL shortener service with APIs, persistence, and analytics.", "greenfield", None),
])
def test_examples_replay_to_green(tmp_path, example, requirement, scope, seed):
    if scope == "greenfield" and not postgres_up():
        pytest.skip("URL shortener integration tests need Postgres (pixi run db-start)")
    repo = tmp_path / "repo"
    if seed:
        shutil.copytree(ROOT / seed, repo, ignore=shutil.ignore_patterns("__pycache__"))

    async def record(kind, payload):
        pass

    answers = ["list endpoint", "constant queries", "no API changes"]
    orch = Orchestrator(repo, scope, Gate("full-auto", never_ask, quiet(), answers), record, tmp_path / "run", quiet(), ROOT / "examples" / example)
    res = asyncio.run(orch.run(requirement))
    assert res.status == "ready_for_review", (tmp_path / "run" / "tests.txt").read_text()
