import asyncio
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic_ai.exceptions import ModelHTTPError, UserError
from pydantic_ai.models import Model
from rich.console import Console

from . import db
from .agents import DEFAULT_MODEL, load_model
from .models import Mode, Scope
from .toolchains import Install
from .orchestrator import Ask, Gate, Orchestrator, Record, Result

app = typer.Typer(add_completion=False, help="Agentic software engineering: requirement in, reviewable change out. "
                  "Run with no command for an interactive session.")


@app.callback(invoke_without_command=True)
def interactive(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        from .session import Session
        Session().loop()


def new_run_dir(requirement: str, out: Path | None) -> Path:
    if out:
        return out
    slug = re.sub(r"[^a-z0-9]+", "-", requirement.lower()).strip("-")[:40]
    return Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"


def workspace(name: str, copy_from: Path | None = None) -> Path:
    """A new timestamped directory under work/, optionally seeded with a copy of an existing codebase. Never overwrites."""
    dest = Path("work") / f"{name}-{datetime.now():%Y%m%d-%H%M%S}"
    if copy_from:
        shutil.copytree(copy_from, dest, ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache"))
    return dest


async def execute(requirement: str, repo: Path, scope: Scope | None, mode: Mode, *, ask: Ask, answers: list[str], replay: Path | None,
                  run_dir: Path, record: Record, console: Console, model: Model | None = None, install: Install = "ask",
                  test_command: str = "") -> Result:
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        return await Orchestrator(repo, scope, Gate(mode, ask, console, answers, install, test_command), record, run_dir, console, replay, model).run(requirement)
    except Exception as e:
        await record("run_finished", {"status": "error", "error": repr(e)})
        raise
    finally:
        (run_dir / "transcript.txt").write_text(console.export_text())


async def _noop(kind: str, payload: dict) -> None:
    pass


def terminal_ask(console: Console) -> Ask:
    def ask(question: str) -> str:
        answer = console.input(question)
        if not sys.stdin.isatty():  # piped answers aren't echoed; print them so the transcript shows the decision
            console.print(answer)
        return answer
    return ask


async def run_once(requirement: str, repo: Path, scope: Scope | None, mode: Mode, *, model_name: str, llm: Model | None,
                   replay: Path | None, answers: list[str], out: Path | None, no_db: bool, console: Console,
                   install: Install = "ask", test_command: str = "") -> Result:
    run_dir = new_run_dir(requirement, out)
    record: Record = _noop
    engine = None
    if not no_db:
        try:
            engine = await db.connect()
        except OSError as e:
            raise typer.BadParameter(f"can't reach Postgres ({e}); run `pixi run db-init`/`db-start`, or pass --no-db")
        run_id, record = await db.start_run(engine, requirement, scope or "auto", mode, str(run_dir))
        console.print(f"[dim]run #{run_id} · {mode} · {'replay' if replay else model_name} · {run_dir}[/]")
    try:
        return await execute(requirement, repo, scope, mode, ask=terminal_ask(console), answers=answers,
                             replay=replay, run_dir=run_dir, record=record, console=console, model=llm, install=install, test_command=test_command)
    finally:
        if engine:
            await engine.dispose()


@app.command()
def run(
    requirement: str,
    repo: Annotated[Path | None, typer.Option(help="Directory the agents work in, edited in place (created if missing). "
                                                   "Default: a new timestamped work/project-<time>.")] = None,
    copy_from: Annotated[Path | None, typer.Option("--from", help="Work on a timestamped copy of this codebase "
                                                   "(work/<name>-<time>) instead of editing it in place.")] = None,
    scope: Annotated[str | None, typer.Option(help="greenfield | brownfield (default: the analyst decides from the repo)")] = None,
    mode: Annotated[str, typer.Option(help="suggest (approve every edit) | auto-edit (approve plan + out-of-scope writes) | full-auto")] = "suggest",
    answer: Annotated[list[str] | None, typer.Option(help="Pre-answer clarifying questions (repeatable).")] = None,
    replay: Annotated[Path | None, typer.Option(help="Replay agent outputs recorded in this run dir instead of calling the model.")] = None,
    out: Annotated[Path | None, typer.Option(help="Run output dir (default runs/<timestamp>-<slug>).")] = None,
    no_db: Annotated[bool, typer.Option("--no-db", help="Skip the Postgres audit trail.")] = False,
    install: Annotated[str, typer.Option(help="When the repo's language toolchain isn't installed: ask | isolated (pixi env under "
                                              "work/.toolchains) | none (skip tests). Nothing is installed globally. "
                                              "A repo's own pixi.toml or native binaries are always used first.")] = "ask",
    test_command: Annotated[str, typer.Option(help="Your own command to run the tests (e.g. a docker or nix command), used as-is "
                                                   "instead of anything detected.")] = "",
    model: Annotated[str, typer.Option(help="Pydantic AI '<provider>:<model>', e.g. google:gemini-flash-latest, anthropic:claude-sonnet-5, openai:gpt-5. "
                                            "The provider reads its own API key from the environment / .env. Default: $AGENTIC_SWE_MODEL.")] = DEFAULT_MODEL,
):
    """Take a requirement through analysis, planning, build, test, validation and summary."""
    if install not in ("ask", "isolated", "none"):
        raise typer.BadParameter("install must be ask|isolated|none")
    if repo and copy_from:
        raise typer.BadParameter("use --repo (edit in place) or --from (edit a copy), not both")
    if copy_from and not copy_from.is_dir():
        raise typer.BadParameter(f"--from {copy_from} is not a directory")
    repo = workspace(copy_from.resolve().name, copy_from) if copy_from else repo or workspace("project")
    if scope not in (None, "greenfield", "brownfield") or mode not in ("suggest", "auto-edit", "full-auto"):
        raise typer.BadParameter("scope must be greenfield|brownfield; mode must be suggest|auto-edit|full-auto")
    llm = None
    if not replay:
        try:
            llm = load_model(model)
        except (UserError, ImportError) as e:
            raise typer.BadParameter(f"{model}: {e}  (or pass --replay <recorded run dir>)")
    console = Console(record=True)
    console.print(f"[dim]working in {repo}[/]")
    try:
        result = asyncio.run(run_once(requirement, repo, scope, mode, model_name=model, llm=llm, replay=replay,
                                      answers=answer or [], out=out, no_db=no_db, console=console, install=install,  # type: ignore[arg-type]
                                      test_command=test_command))
    except ModelHTTPError as e:
        console.print(f"[red]{model} failed after retries: HTTP {e.status_code}[/] {str(e.body)[:300]}")
        raise typer.Exit(1)
    raise typer.Exit(0 if result.status in ("ready_for_review", "needs_review") else 1)


@app.command()
def runs():
    """List recent runs from the audit trail."""
    async def main():
        engine = await db.connect()
        rows = await db.list_runs(engine)
        await engine.dispose()
        return rows
    for r in asyncio.run(main()):
        typer.echo(f"#{r['id']:<4} {r['status']:<17} {r['mode']:<10} {r['scope']:<10} {r['requirement'][:60]}")


@app.command()
def show(run_id: int):
    """Print a run's full event log."""
    async def main():
        engine = await db.connect()
        row = await db.get_run(engine, run_id)
        await engine.dispose()
        return row
    typer.echo(json.dumps(asyncio.run(main()), indent=2))


if __name__ == "__main__":
    app()
