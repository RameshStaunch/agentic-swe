"""One pydantic-ai Agent per SDLC role. Each has a typed output so every hand-off is schema-validated."""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pydantic_monty
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model, infer_model

from .models import CodeChange, DesignDoc, EngineeringSummary, ImpactAnalysis, NormalizedRequirement, TaskGraph, ValidationReport

DEFAULT_MODEL = os.environ.get("AGENTIC_SWE_MODEL", "openrouter:qwen/qwen3.8-27b:free")
MAX_READ = 20_000


def load_model(name: str) -> Model:
    """Resolve a Pydantic AI '<provider>:<model>' string. The provider reads its own key (GOOGLE_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) and raises if it is missing."""
    if name == "mock":
        from .mock import mock_model
        return mock_model()
    return infer_model(name)


@dataclass
class Repo:
    root: Path

    def resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ModelRetry(f"{rel} is outside the repository")
        return p


def list_files(ctx: RunContext[Repo]) -> list[str]:
    """List every file in the repository (repo-relative paths)."""
    root = ctx.deps.root
    return sorted(
        str(p.relative_to(root)) for p in root.rglob("*")
        if p.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in p.relative_to(root).parts)
    )


def read_file(ctx: RunContext[Repo], path: str) -> str:
    """Read one file from the repository."""
    p = ctx.deps.resolve(path)
    if not p.is_file():
        raise ModelRetry(f"{path} does not exist; call list_files first")
    return p.read_text()[:MAX_READ]


def grep(ctx: RunContext[Repo], pattern: str) -> str:
    """Search the repository for a regex; returns path:line:text matches."""
    try:
        re.compile(pattern)
    except re.error as e:
        raise ModelRetry(f"bad regex: {e}")
    out = subprocess.run(["grep", "-rnE", "--exclude-dir=.*", "--exclude-dir=__pycache__", pattern, "."],
                         cwd=ctx.deps.root, capture_output=True, text=True, timeout=10).stdout
    return out[:MAX_READ] or "no matches"


def run_python(code: str) -> str:
    """Run a Python snippet in the Monty sandbox to check a regex, an algorithm or a calculation. Only a small stdlib subset
    (re, json, math, dataclasses, ...) is available: no third-party packages, filesystem or network. Returns printed output
    and the value of the last expression."""
    out = pydantic_monty.CollectString(max_bytes=MAX_READ)
    try:
        with pydantic_monty.Monty() as pool, pool.checkout(limits={"max_feed_duration_secs": 5, "max_memory": 64 * 1024 * 1024}) as session:
            value = session.feed_run(code, print_callback=out)
    except pydantic_monty.MontyError as e:
        return f"{out.output}\nerror: {e}"[:MAX_READ]
    return f"{out.output}\n=> {value!r}"[:MAX_READ]


REPO_TOOLS = [list_files, read_file, grep, run_python]

analyst = Agent(
    output_type=NormalizedRequirement, name="requirement-analyst",
    instructions=(
        "You are a staff engineer turning a raw requirement into an engineering problem statement. "
        "Be concrete. Flag an ambiguity only when two reasonable engineers would build materially different things; "
        "vague performance or quality words ('faster', 'better', 'scalable') without a target are ambiguities. "
        "For every ambiguity give a clarifying question and the assumption you would default to."
    ),
)

decomposer = Agent(
    output_type=TaskGraph, name="task-decomposer",
    instructions=(
        "Break the requirement into 3-8 engineering tasks forming a DAG. Every task declares the exact files it may touch; "
        "tasks that can run in parallel must not share files. Tests are their own tasks and depend on the code they test. "
        "Include a docs task. Keep file paths repo-relative."
    ),
)

architect = Agent(
    output_type=DesignDoc, name="architect",
    instructions=(
        "Design a greenfield service for the requirement: components, a complete HTTP API contract, the data model as SQL DDL, "
        "and the key decisions with the trade-off each makes. Prefer boring, proven choices."
    ),
)

reasoner = Agent(
    output_type=ImpactAnalysis, deps_type=Repo, tools=REPO_TOOLS, name="codebase-reasoner",
    instructions=(
        "You are onboarding onto an existing codebase. Use the tools to read the code before concluding anything. "
        "Identify exactly which files, APIs and data flows the requirement touches, and what could break."
    ),
)

coder = Agent(
    output_type=CodeChange, deps_type=Repo, tools=REPO_TOOLS, name="coder", retries=2,
    instructions=(
        "You implement one task. Output the FULL content of every file you create or change, and only files the task lists. "
        "Write production-quality, typed Python that follows the existing code's conventions. "
        "Tests use pytest and must be runnable with `pytest` from the repository root."
    ),
)

validator = Agent(
    output_type=ValidationReport, name="validator",
    instructions=(
        "You review a change before it ships. Given the requirement, plan, diff and test results, list concrete risks, "
        "trade-offs, failure scenarios and the guardrails that mitigate them. Recommend 'revise' if tests fail or the diff "
        "does not meet the acceptance criteria."
    ),
)

summarizer = Agent(
    output_type=EngineeringSummary, name="summarizer",
    instructions="Write the final engineering summary for a reviewer who has five minutes. Plain language, no filler.",
)
