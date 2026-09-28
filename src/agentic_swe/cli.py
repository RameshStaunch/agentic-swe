import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import db
from .agents import PROVIDER_KEYS
from .models import Mode, Scope
from .orchestrator import Ask, Gate, Orchestrator, Record, Result

app = typer.Typer(add_completion=False, help="Agentic software engineering: requirement in, reviewable change out.")


def new_run_dir(requirement: str, out: Path | None) -> Path:
    if out:
        return out
    slug = re.sub(r"[^a-z0-9]+", "-", requirement.lower()).strip("-")[:40]
    return Path("runs") / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"


async def execute(requirement: str, repo: Path, scope: Scope, mode: Mode, *, ask: Ask, answers: list[str], replay: Path | None,
                  run_dir: Path, record: Record, console: Console) -> Result:
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        return await Orchestrator(repo, scope, Gate(mode, ask, console, answers), record, run_dir, console, replay).run(requirement)
    except Exception as e:
        await record("run_finished", {"status": "error", "error": repr(e)})
        raise
    finally:
        (run_dir / "transcript.txt").write_text(console.export_text())


async def _noop(kind: str, payload: dict) -> None:
    pass


@app.command()
def run(
    requirement: str,
    repo: Annotated[Path, typer.Option(help="Repository the agents work in (created if missing).")],
    scope: Annotated[str, typer.Option(help="greenfield | brownfield")] = "greenfield",
    mode: Annotated[str, typer.Option(help="suggest (approve every edit) | auto-edit (approve plan + out-of-scope writes) | full-auto")] = "suggest",
    answer: Annotated[list[str] | None, typer.Option(help="Pre-answer clarifying questions (repeatable).")] = None,
    replay: Annotated[Path | None, typer.Option(help="Replay agent outputs recorded in this run dir instead of calling the model.")] = None,
    out: Annotated[Path | None, typer.Option(help="Run output dir (default runs/<timestamp>-<slug>).")] = None,
    no_db: Annotated[bool, typer.Option("--no-db", help="Skip the Postgres audit trail.")] = False,
):
    """Take a requirement through analysis, planning, build, test, validation and summary."""
    if scope not in ("greenfield", "brownfield") or mode not in ("suggest", "auto-edit", "full-auto"):
        raise typer.BadParameter("scope must be greenfield|brownfield; mode must be suggest|auto-edit|full-auto")
    if not replay and not any(os.environ.get(k) for k in PROVIDER_KEYS):
        raise typer.BadParameter("set GOOGLE_API_KEY or ANTHROPIC_API_KEY (e.g. in .env) or pass --replay <recorded run dir>")
    console = Console(record=True)
    run_dir = new_run_dir(requirement, out)

    def ask(question: str) -> str:
        answer = console.input(question)
        if not sys.stdin.isatty():  # piped answers aren't echoed; print them so the transcript shows the decision
            console.print(answer)
        return answer

    async def main():
        record: Record = _noop
        engine = None
        if not no_db:
            try:
                engine = await db.connect()
            except OSError as e:
                raise typer.BadParameter(f"can't reach Postgres ({e}); run `pixi run db-init`/`db-start`, or pass --no-db")
            run_id, record = await db.start_run(engine, requirement, scope, mode, str(run_dir))
            console.print(f"[dim]run #{run_id} · {mode} · {run_dir}[/]")
        try:
            return await execute(requirement, repo, scope, mode, ask=ask, answers=answer or [],
                                 replay=replay, run_dir=run_dir, record=record, console=console)
        finally:
            if engine:
                await engine.dispose()

    result = asyncio.run(main())
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
