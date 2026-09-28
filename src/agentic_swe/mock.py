"""Offline stand-in for an LLM. It answers every agent from the recorded examples, but through the real Pydantic AI
agent loop (output tools, schema validation, the decomposer's graph validator), so the live code path is exercised
without network calls or API keys."""

import json
import re
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
ROLES = {  # a phrase from each agent's instructions -> recorded step name
    "turning a raw requirement": "1-analyst",
    "Design a greenfield": "2-architect",
    "onboarding onto an existing codebase": "2-reasoner",
    "Break the requirement": "3-decomposer",
    "You implement one task": "coder",
    "You review a change": "6-validator",
    "final engineering summary": "7-summary",
}
KEYWORDS = [  # requirement words -> the recorded example the mock answers from
    (("faster", "slow", "perf", "latency", "speed"), "ambiguous-make-it-faster"),
    (("paginat", "page", "cursor"), "brownfield-pagination"),
    (("todo", "title"), "brownfield-go-validation"),
]
DEFAULT_EXAMPLE = "greenfield-url-shortener"


def _prompt(messages: list[ModelMessage]) -> str:
    for m in messages:
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    return p.content
    return ""


def mock_model() -> FunctionModel:
    state = {"example": DEFAULT_EXAMPLE}

    def load(step: str) -> dict | None:
        path = EXAMPLES / state["example"] / "steps" / f"{step}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        role = next(v for k, v in ROLES.items() if k in (info.instructions or ""))
        prompt = _prompt(messages)
        if role == "1-analyst":
            if "Answers so far" in prompt:  # follow-up round: the answers resolved everything
                out = load("1-analyst") | {"ambiguities": [], "clarifying_questions": []}
            else:
                req = prompt.split("Requirement:", 1)[-1].split("\n", 1)[0].lower()
                state["example"] = next((ex for words, ex in KEYWORDS if any(w in req for w in words)), DEFAULT_EXAMPLE)
                out = load("1-analyst")
        elif role == "coder":
            task_id = re.search(r'"id":\s*"([^"]+)"', prompt.split("Your task:", 1)[-1])
            tid = task_id.group(1) if task_id else ""
            out = load(f"5-{tid}" if tid.startswith("fix-") else f"4-task-{tid}") or {"files": [], "notes": f"mock: nothing recorded for {tid}"}
        else:
            out = load(role)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])

    return FunctionModel(respond, model_name="mock")
