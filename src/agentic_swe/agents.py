"""One pydantic-ai Agent per SDLC role. Each has a typed output so every hand-off is schema-validated."""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent, ModelRetry, RunContext

from .models import CodeChange, DesignDoc, EngineeringSummary, ImpactAnalysis, NormalizedRequirement, TaskGraph, ValidationReport

MODEL = os.environ.get("AGENTIC_SWE_MODEL") or (
    "google:gemini-flash-latest" if os.environ.get("GOOGLE_API_KEY") else "anthropic:claude-sonnet-5")
PROVIDER_KEYS = ("GOOGLE_API_KEY", "ANTHROPIC_API_KEY")
MAX_READ = 20_000


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


REPO_TOOLS = [list_files, read_file, grep]

analyst = Agent(
    MODEL, output_type=NormalizedRequirement, defer_model_check=True, name="requirement-analyst",
    instructions=(
        "You are a staff engineer turning a raw requirement into an engineering problem statement. "
        "Be concrete. Flag an ambiguity only when two reasonable engineers would build materially different things; "
        "vague performance or quality words ('faster', 'better', 'scalable') without a target are ambiguities. "
        "For every ambiguity give a clarifying question and the assumption you would default to."
    ),
)

decomposer = Agent(
    MODEL, output_type=TaskGraph, defer_model_check=True, name="task-decomposer",
    instructions=(
        "Break the requirement into 3-8 engineering tasks forming a DAG. Every task declares the exact files it may touch; "
        "tasks that can run in parallel must not share files. Tests are their own tasks and depend on the code they test. "
        "Include a docs task. Keep file paths repo-relative."
    ),
)

architect = Agent(
    MODEL, output_type=DesignDoc, defer_model_check=True, name="architect",
    instructions=(
        "Design a greenfield service for the requirement: components, a complete HTTP API contract, the data model as SQL DDL, "
        "and the key decisions with the trade-off each makes. Prefer boring, proven choices."
    ),
)

reasoner = Agent(
    MODEL, output_type=ImpactAnalysis, deps_type=Repo, tools=REPO_TOOLS, defer_model_check=True, name="codebase-reasoner",
    instructions=(
        "You are onboarding onto an existing codebase. Use the tools to read the code before concluding anything. "
        "Identify exactly which files, APIs and data flows the requirement touches, and what could break."
    ),
)

coder = Agent(
    MODEL, output_type=CodeChange, deps_type=Repo, tools=REPO_TOOLS, defer_model_check=True, name="coder", retries=2,
    instructions=(
        "You implement one task. Output the FULL content of every file you create or change, and only files the task lists. "
        "Write production-quality, typed Python that follows the existing code's conventions. "
        "Tests use pytest and must be runnable with `pytest` from the repository root."
    ),
)

validator = Agent(
    MODEL, output_type=ValidationReport, defer_model_check=True, name="validator",
    instructions=(
        "You review a change before it ships. Given the requirement, plan, diff and test results, list concrete risks, "
        "trade-offs, failure scenarios and the guardrails that mitigate them. Recommend 'revise' if tests fail or the diff "
        "does not meet the acceptance criteria."
    ),
)

summarizer = Agent(
    MODEL, output_type=EngineeringSummary, defer_model_check=True, name="summarizer",
    instructions="Write the final engineering summary for a reviewer who has five minutes. Plain language, no filler.",
)
