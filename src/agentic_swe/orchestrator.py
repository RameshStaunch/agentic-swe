"""Drives a requirement through the agent pipeline: analyse -> plan -> design/impact -> build (DAG) -> test/fix -> validate -> summarise."""

import asyncio
import difflib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import ModelHTTPError
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from . import agents
from .agents import Repo
from .models import CodeChange, DesignDoc, EngineeringSummary, FileWrite, ImpactAnalysis, Mode, NormalizedRequirement, Scope, Task, TaskGraph, ValidationReport

MAX_FIX_ATTEMPTS = 2
MAX_REDO = 2
PROTECTED = {".git", ".env", ".pixi"}

Record = Callable[[str, dict], Awaitable[None]]
Ask = Callable[[str], str]


def validate_graph(graph: TaskGraph) -> list[str]:
    """Structural checks on a plan; returned errors are sent back to the decomposer to fix."""
    errors = []
    ids = [t.id for t in graph.tasks]
    if len(ids) != len(set(ids)):
        errors.append("task ids must be unique")
    known = set(ids)
    for t in graph.tasks:
        for d in t.depends_on:
            if d not in known:
                errors.append(f"{t.id} depends on unknown task {d}")
        if not t.files:
            errors.append(f"{t.id} declares no files")
    if not errors and not waves(graph):
        errors.append("dependency cycle")
    ancestors = {t.id: _ancestors(graph, t.id) for t in graph.tasks}
    for i, a in enumerate(graph.tasks):
        for b in graph.tasks[i + 1:]:
            ordered = a.id in ancestors.get(b.id, set()) or b.id in ancestors.get(a.id, set())
            shared = set(a.files) & set(b.files)
            if shared and not ordered:
                errors.append(f"{a.id} and {b.id} can run in parallel but both touch {sorted(shared)}")
    return errors


def _ancestors(graph: TaskGraph, tid: str, seen: set[str] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    deps = next((t.depends_on for t in graph.tasks if t.id == tid), [])
    for d in deps:
        if d not in seen:
            seen.add(d)
            _ancestors(graph, d, seen)
    return seen


def waves(graph: TaskGraph) -> list[list[Task]]:
    """Topological layers: every task in a wave only depends on earlier waves. Empty list on a cycle."""
    done: set[str] = set()
    remaining = list(graph.tasks)
    out = []
    while remaining:
        ready = [t for t in remaining if set(t.depends_on) <= done]
        if not ready:
            return []
        out.append(ready)
        done |= {t.id for t in ready}
        remaining = [t for t in remaining if t.id not in done]
    return out


@agents.decomposer.output_validator
def _check_graph(graph: TaskGraph) -> TaskGraph:
    if errors := validate_graph(graph):
        raise ModelRetry("Fix the task graph: " + "; ".join(errors))
    return graph


class Gate:
    """The single place autonomy is enforced. Every side effect asks the gate first."""

    def __init__(self, mode: Mode, ask: Ask, console: Console, answers: list[str] | None = None):
        self.mode, self.ask, self.console, self.answers = mode, ask, console, answers or []
        self._lock = asyncio.Lock()

    async def _prompt(self, question: str) -> str:
        async with self._lock:
            return (await asyncio.to_thread(self.ask, question)).strip()

    async def clarify(self, req: NormalizedRequirement) -> list[str]:
        if not req.ambiguities:
            return []
        if self.answers:
            return self.answers
        if self.mode == "full-auto":
            return [f"(no answer, assumed) {a}" for a in req.assumptions]
        out = []
        for q, default in zip(req.clarifying_questions, req.assumptions + [""] * len(req.clarifying_questions)):
            ans = await self._prompt(f"[bold yellow]?[/] {q}\n  [dim]enter = {default}[/]\n> ")
            out.append(f"Q: {q} A: {ans or default}")
        return out

    async def approve_plan(self) -> bool:
        if self.mode == "full-auto":
            return True
        return (await self._prompt("Approve this plan? [y/n] ")).lower().startswith("y")

    async def review_writes(self, task: Task, repo: Path, writes: list[FileWrite]) -> tuple[list[FileWrite], str | None]:
        """Returns (writes to apply, reviewer feedback). Feedback means: redo the task."""
        allowed = []
        for w in writes:
            target = (repo / w.path).resolve()
            if not target.is_relative_to(repo.resolve()) or PROTECTED & set(Path(w.path).parts):
                self.console.print(f"  [red]blocked[/] write to {w.path}: outside repo or protected")
                continue
            if w.path not in task.files:
                if self.mode == "full-auto" or not (await self._prompt(
                        f"  [yellow]{task.id} wants to write {w.path}, which is outside its declared scope. Allow? [y/n] ")).lower().startswith("y"):
                    self.console.print(f"  [red]rejected[/] out-of-scope write {w.path}")
                    continue
            allowed.append(w)
        if self.mode != "suggest" or not allowed:
            return allowed, None
        for w in allowed:
            self.console.print(Syntax(_diff(repo / w.path, w.content, w.path) or "(no change)", "diff", word_wrap=True))
        ans = await self._prompt(f"Apply {len(allowed)} file(s) for {task.id}? [y / n / or type feedback to redo] ")
        if ans.lower() in ("y", "yes", ""):
            return allowed, None
        if ans.lower() in ("n", "no"):
            return [], None
        return [], ans

    async def escalate(self, message: str) -> bool:
        """Something failed past automatic recovery. Returns True to keep going."""
        self.console.print(f"[bold red]Escalation:[/] {message}")
        if self.mode == "full-auto":
            return True
        return (await self._prompt("Continue to validation anyway? [y/n] ")).lower().startswith("y")


def _diff(path: Path, new: str, label: str) -> str:
    old = path.read_text() if path.exists() else ""
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{label}", f"b/{label}"))


def run_tests(repo: Path) -> tuple[bool, str]:
    errors = []
    for p in repo.rglob("*.py"):
        try:
            compile(p.read_text(), str(p), "exec")
        except SyntaxError as e:
            errors.append(f"{p.relative_to(repo)}:{e.lineno}: {e.msg}")
    if errors:
        return False, "syntax errors:\n" + "\n".join(errors)
    # A fresh bytecode cache per run: a same-size edit within the same second would otherwise reuse stale .pyc files.
    with tempfile.TemporaryDirectory() as pycache:
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo, capture_output=True,
                               text=True, timeout=600, env={**os.environ, "PYTHONPYCACHEPREFIX": pycache})
        except subprocess.TimeoutExpired:
            return False, "pytest timed out after 600s"
    out = (r.stdout + r.stderr)[-8000:]
    return r.returncode == 0, out if r.returncode != 5 else "no tests collected\n" + out


class Steps:
    """Every agent output is saved as steps/<name>.json. With a replay dir, outputs are read back instead of calling the model."""

    def __init__(self, run_dir: Path, replay: Path | None):
        self.dir, self.replay = run_dir / "steps", replay
        self.dir.mkdir(parents=True, exist_ok=True)

    async def call[T: BaseModel](self, name: str, agent: Agent[..., T], prompt: str, deps: Repo | None = None) -> T:
        out_type = agent.output_type
        if self.replay:
            src = self.replay / "steps" / f"{name}.json"
            if not src.exists():
                raise RuntimeError(f"replay has no recorded step '{name}' (the run diverged from the recording)")
            out = out_type.model_validate_json(src.read_text())
        else:
            for attempt in range(4):
                try:
                    out = (await agent.run(prompt, deps=deps)).output
                    break
                except ModelHTTPError as e:  # rate limits and provider overload are transient
                    if e.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                        raise
                    await asyncio.sleep(5 * 2 ** attempt)
        (self.dir / f"{name}.json").write_text(out.model_dump_json(indent=2))
        return out


@dataclass
class Result:
    status: str
    requirement: NormalizedRequirement | None = None
    graph: TaskGraph | None = None
    task_status: dict[str, str] = field(default_factory=dict)
    tests_passed: bool = False
    validation: ValidationReport | None = None
    summary: EngineeringSummary | None = None
    run_dir: Path | None = None


def dump(m: BaseModel | None) -> str:
    return m.model_dump_json(indent=2) if m else "n/a"


class Orchestrator:
    def __init__(self, repo: Path, scope: Scope, gate: Gate, record: Record, run_dir: Path, console: Console, replay: Path | None = None):
        self.repo, self.scope, self.gate, self.record, self.run_dir, self.console = repo.resolve(), scope, gate, record, run_dir, console
        self.steps = Steps(run_dir, replay)
        self.originals: dict[str, str] = {}
        self.written: dict[str, str] = {}

    async def run(self, requirement: str) -> Result:
        res = Result(status="running", run_dir=self.run_dir)
        c = self.console
        self.repo.mkdir(parents=True, exist_ok=True)
        await self.record("run_started", {"requirement": requirement, "scope": self.scope, "mode": self.gate.mode, "repo": str(self.repo)})

        # 1. Understand
        c.rule("[bold]1 · Requirement analysis")
        req = await self.steps.call("1-analyst", agents.analyst, f"Scope: {self.scope}\nRequirement: {requirement}")
        res.requirement = req
        await self.record("requirement", req.model_dump())
        c.print(Panel(f"[bold]{req.intent}[/]\n\n" + "\n".join(f"• {a}" for a in req.acceptance_criteria), title="Normalized requirement"))
        if req.ambiguities:
            c.print(Panel("\n".join(f"• {a}" for a in req.ambiguities), title="[yellow]Ambiguities", border_style="yellow"))
        clarifications = await self.gate.clarify(req)
        if clarifications:
            await self.record("clarification", {"answers": clarifications})
            c.print(Panel("\n".join(clarifications), title="Clarifications"))

        # 2. Understand the system (design for greenfield, impact analysis for brownfield)
        context: DesignDoc | ImpactAnalysis
        brief = f"Requirement:\n{dump(req)}\nClarifications:\n" + "\n".join(clarifications or ["none"])
        if self.scope == "greenfield":
            c.rule("[bold]2 · Architecture")
            context = await self.steps.call("2-architect", agents.architect, brief)
            t = Table("Method", "Path", "Response", title="API contract")
            for e in context.api_contract:
                t.add_row(e.method, e.path, e.response)
            c.print(t)
            c.print(Panel("\n".join(f"• {d}" for d in context.key_decisions), title="Key decisions"))
        else:
            c.rule("[bold]2 · Codebase impact analysis")
            context = await self.steps.call("2-reasoner", agents.reasoner, brief + f"\nRepository root: {self.repo.name}", deps=Repo(self.repo))
            c.print(Panel(context.summary + "\n\n[bold]Files:[/] " + ", ".join(context.impacted_files)
                          + "\n[bold]APIs:[/] " + ", ".join(context.impacted_apis), title="Impact"))
        await self.record("context", {"kind": type(context).__name__, **context.model_dump()})

        # 3. Plan
        c.rule("[bold]3 · Task decomposition")
        existing = "\n".join(sorted(str(p.relative_to(self.repo)) for p in self.repo.rglob("*.py") if "__pycache__" not in p.parts)) or "(empty repository)"
        graph = await self.steps.call("3-decomposer", agents.decomposer, f"{brief}\n\nSystem context:\n{dump(context)}\n\nExisting files:\n{existing}")
        res.graph = graph
        await self.record("plan", graph.model_dump())
        self._print_plan(graph)
        if not await self.gate.approve_plan():
            await self.record("plan_rejected", {})
            res.status = "plan_rejected"
            return res

        # 4. Build, wave by wave; tasks in a wave run concurrently
        c.rule("[bold]4 · Build")
        status: dict[str, str] = {t.id: "pending" for t in graph.tasks}
        for n, wave in enumerate(waves(graph), 1):
            c.print(f"[dim]wave {n}: {', '.join(t.id for t in wave)}[/]")
            await asyncio.gather(*(self._run_task(t, graph, status, brief, context) for t in wave))
        res.task_status = status

        # 5. Test, and feed failures back as fix tasks
        c.rule("[bold]5 · Test & recover")
        passed, output = run_tests(self.repo)
        for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
            await self.record("tests", {"attempt": attempt - 1, "passed": passed, "output": output[-3000:]})
            c.print(f"tests: {'[green]passed' if passed else '[red]failed'}[/]")
            if passed:
                break
            c.print(Panel(output[-1500:], title=f"failure → fix attempt {attempt}", border_style="red"))
            await self._fix(attempt, output, graph, brief)
            passed, output = run_tests(self.repo)
        else:
            await self.record("tests", {"attempt": MAX_FIX_ATTEMPTS, "passed": passed, "output": output[-3000:]})
            c.print(f"tests: {'[green]passed' if passed else '[red]failed'}[/]")
        (self.run_dir / "tests.txt").write_text(output)
        res.tests_passed = passed
        if not passed and not await self.gate.escalate(f"tests still failing after {MAX_FIX_ATTEMPTS} fix attempts"):
            await self.record("run_finished", {"status": "stopped"})
            res.status = "stopped"
            return res

        # 6. Validate
        c.rule("[bold]6 · Validation & risk review")
        patch = self._patch()
        (self.run_dir / "changes.patch").write_text(patch)
        validation = await self.steps.call("6-validator", agents.validator,
            f"{brief}\n\nPlan:\n{dump(graph)}\n\nTask status: {json.dumps(status)}\n\nTests passed: {passed}\n{output[-3000:]}\n\nDiff:\n{patch[:40000]}")
        res.validation = validation
        await self.record("validation", validation.model_dump())
        colour = "green" if validation.recommendation == "approve" else "yellow"
        c.print(Panel("\n".join(f"• {r}" for r in validation.risks), title=f"[{colour}]Validator: {validation.recommendation}", border_style=colour))

        # 7. Summarise
        c.rule("[bold]7 · Summary")
        summary = await self.steps.call("7-summary", agents.summarizer,
            f"{brief}\n\nContext:\n{dump(context)}\n\nPlan:\n{dump(graph)}\n\nTask status: {json.dumps(status)}\n\nTests passed: {passed}\n\nValidation:\n{dump(validation)}")
        res.summary = summary
        res.status = "needs_review" if not passed or validation.recommendation == "revise" or "failed" in status.values() else "ready_for_review"
        (self.run_dir / "summary.md").write_text(render_summary(requirement, res, context, clarifications, list(self.written)))
        await self.record("run_finished", {"status": res.status})
        c.print(f"[bold]{res.status}[/] → {self.run_dir / 'summary.md'}")
        return res

    async def _run_task(self, task: Task, graph: TaskGraph, status: dict[str, str], brief: str, context: BaseModel) -> None:
        blocked = [d for d in task.depends_on if status[d] != "done"]
        if blocked:
            status[task.id] = "blocked"
            await self.record("task_blocked", {"task": task.id, "by": blocked})
            self.console.print(f"  [yellow]⊘ {task.id}[/] blocked by {', '.join(blocked)}")
            return
        await self.record("task_started", {"task": task.id})
        dep_files = {f for d in task.depends_on for t in graph.tasks if t.id == d for f in t.files}
        feedback = None
        for attempt in range(MAX_REDO + 1):
            prompt = "\n\n".join([
                brief, f"System context:\n{dump(context)}", f"Your task:\n{dump(task)}",
                "Files produced by the tasks you depend on:\n" + self._show(dep_files),
                "Current content of your files:\n" + self._show(set(task.files)),
                f"Reviewer feedback on your previous attempt: {feedback}" if feedback else "",
            ])
            step = f"4-task-{task.id}" + (f"-redo{attempt}" if attempt else "")
            try:
                change = await self.steps.call(step, agents.coder, prompt, deps=Repo(self.repo))
            except Exception as e:  # agent/network/validation failure: record, retry once, then fail the task
                await self.record("task_error", {"task": task.id, "attempt": attempt, "error": repr(e)})
                self.console.print(f"  [red]✗ {task.id}[/] {e!r}")
                if attempt >= 1:
                    status[task.id] = "failed"
                    return
                continue
            writes, feedback = await self.gate.review_writes(task, self.repo, change.files)
            if feedback:
                await self.record("task_feedback", {"task": task.id, "feedback": feedback})
                continue
            self._apply(task, writes)
            status[task.id] = "done" if writes or not change.files else "rejected"
            await self.record("task_done", {"task": task.id, "status": status[task.id], "files": [w.path for w in writes], "notes": change.notes})
            self.console.print(f"  [green]✓ {task.id}[/] {', '.join(w.path for w in writes) or '(nothing written)'}")
            return
        status[task.id] = "failed"

    async def _fix(self, attempt: int, output: str, graph: TaskGraph, brief: str) -> None:
        files = sorted(set(self.written) | {f for t in graph.tasks for f in t.files})
        fix = Task(id=f"fix-{attempt}", title="Make the test suite pass", kind="codegen", files=files,
                   description="The tests fail. Find the root cause and fix the code (or the test, if the test is wrong). Change as little as possible.")
        await self.record("fix_attempt", {"attempt": attempt})
        try:
            change = await self.steps.call(f"5-fix-{attempt}", agents.coder, "\n\n".join([
                brief, f"Your task:\n{dump(fix)}", f"Test output:\n{output[-6000:]}", "Current files:\n" + self._show(set(self.written))]),
                deps=Repo(self.repo))
        except Exception as e:
            await self.record("task_error", {"task": fix.id, "error": repr(e)})
            return
        writes, _ = await self.gate.review_writes(fix, self.repo, change.files)
        self._apply(fix, writes)

    def _apply(self, task: Task, writes: list[FileWrite]) -> None:
        for w in writes:
            p = self.repo / w.path
            self.originals.setdefault(w.path, p.read_text() if p.exists() else "")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(w.content)
            self.written[w.path] = task.id

    def _show(self, paths: set[str]) -> str:
        parts = [f"--- {p}\n{(self.repo / p).read_text()[:agents.MAX_READ]}" for p in sorted(paths) if (self.repo / p).is_file()]
        return "\n".join(parts) or "(none yet)"

    def _patch(self) -> str:
        return "".join(
            "".join(difflib.unified_diff(self.originals[p].splitlines(True), (self.repo / p).read_text().splitlines(True),
                                         f"a/{self.repo.name}/{p}", f"b/{self.repo.name}/{p}"))
            for p in sorted(self.written))

    def _print_plan(self, graph: TaskGraph) -> None:
        t = Table("Task", "Kind", "Depends on", "Files", title="Task graph")
        for task in graph.tasks:
            t.add_row(f"[bold]{task.id}[/]\n{task.title}", task.kind, ", ".join(task.depends_on) or "-", "\n".join(task.files))
        self.console.print(t)
        self.console.print(f"[dim]{graph.rationale}[/]")
        self.console.print("[dim]execution waves: " + " → ".join("(" + ", ".join(t.id for t in w) + ")" for w in waves(graph)) + "[/]")


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) or "- none"


def render_summary(raw: str, res: Result, context: BaseModel, clarifications: list[str], files: list[str]) -> str:
    s, req, v, g = res.summary, res.requirement, res.validation, res.graph
    assert s and req and v and g
    ctx = ""
    if isinstance(context, DesignDoc):
        rows = "\n".join(f"| `{e.method}` | `{e.path}` | {e.request} | {e.response} |" for e in context.api_contract)
        ctx = (f"## Architecture\n\n**Components**\n\n{_bullets(context.components)}\n\n**API contract**\n\n| Method | Path | Request | Response |\n|---|---|---|---|\n{rows}\n\n"
               f"**Data model**\n\n```sql\n{context.data_model}\n```\n\n**Key decisions**\n\n{_bullets(context.key_decisions)}\n")
    elif isinstance(context, ImpactAnalysis):
        ctx = (f"## Impact analysis\n\n{context.summary}\n\n**Impacted files**\n\n{_bullets(context.impacted_files)}\n\n**Impacted APIs**\n\n{_bullets(context.impacted_apis)}\n\n"
               f"**Data-flow changes**\n\n{_bullets(context.data_flow_changes)}\n\n**Risk notes**\n\n{_bullets(context.risk_notes)}\n")
    tasks = "\n".join(f"| {t.id} | {t.kind} | {', '.join(t.depends_on) or '-'} | {res.task_status.get(t.id, '-')} |" for t in g.tasks)
    return f"""# Engineering summary

**Requirement:** {raw}
**Status:** {res.status} ({'tests passing' if res.tests_passed else 'tests FAILING'}, validator says **{v.recommendation}**)

## Plan

{s.plan}

**Why this plan:** {s.rationale}

## Understanding

{req.intent}

**Acceptance criteria**

{_bullets(req.acceptance_criteria)}

**Ambiguities found**

{_bullets(req.ambiguities)}

**Clarifications used**

{_bullets(clarifications)}

{ctx}
## Tasks

{g.rationale}

| Task | Kind | Depends on | Result |
|---|---|---|---|
{tasks}

## Artifacts

{_bullets([f'`{f}`' for f in files])}

Full diff: `changes.patch`. Test output: `tests.txt`. Every agent output: `steps/`.

## Risks

{_bullets(s.risks + [r for r in v.risks if r not in s.risks])}

## Trade-offs

{_bullets(s.trade_offs + [t for t in v.trade_offs if t not in s.trade_offs])}

## Failure scenarios and guardrails

{_bullets(v.failure_scenarios)}

{_bullets(v.guardrails)}

## Assumptions

{_bullets(s.assumptions)}

## Limitations

{_bullets(s.limitations)}
"""
