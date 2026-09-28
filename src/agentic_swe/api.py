"""HTTP front door to the orchestrator. Runs are full-auto (no human at a terminal); humans review the result via GET /runs/{id}."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydantic_ai.exceptions import UserError
from rich.console import Console

from . import db
from .agents import DEFAULT_MODEL, load_model
from .cli import execute, new_run_dir
from .models import Scope

WORKSPACE = Path.cwd().resolve()
_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = await db.connect()
    yield
    await app.state.engine.dispose()


app = FastAPI(title="agentic-swe", lifespan=lifespan)


class RunRequest(BaseModel):
    requirement: str
    repo: str
    scope: Scope = "greenfield"
    answers: list[str] = []
    replay: str | None = None
    model: str = DEFAULT_MODEL


def _inside_workspace(rel: str) -> Path:
    p = (WORKSPACE / rel).resolve()
    if not p.is_relative_to(WORKSPACE):
        raise HTTPException(400, f"{rel} is outside the workspace")
    return p


def _no_terminal(question: str) -> str:
    raise RuntimeError("API runs are full-auto; nothing should prompt")


@app.post("/runs", status_code=202)
async def create_run(body: RunRequest) -> dict:
    repo = _inside_workspace(body.repo)
    replay = _inside_workspace(body.replay) if body.replay else None
    llm = None
    if not replay:
        try:
            llm = load_model(body.model)
        except (UserError, ImportError) as e:
            raise HTTPException(400, f"{body.model}: {e}")
    run_dir = new_run_dir(body.requirement, None)
    run_id, record = await db.start_run(app.state.engine, body.requirement, body.scope, "full-auto", str(run_dir))
    task = asyncio.create_task(execute(body.requirement, repo, body.scope, "full-auto", ask=_no_terminal, answers=body.answers,
                                       replay=replay, run_dir=run_dir, record=record, console=Console(record=True, quiet=True), model=llm))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"id": run_id, "run_dir": str(run_dir)}


@app.get("/runs")
async def list_runs() -> list[dict]:
    return await db.list_runs(app.state.engine)


@app.get("/runs/{run_id}")
async def get_run(run_id: int) -> dict:
    run = await db.get_run(app.state.engine, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    summary = Path(run["run_dir"]) / "summary.md"
    run["summary_md"] = summary.read_text() if summary.exists() else None
    return run
