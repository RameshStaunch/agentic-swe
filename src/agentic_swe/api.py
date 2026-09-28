"""HTTP front door to the orchestrator. Runs are full-auto (no human at a terminal); humans review the result via GET /runs/{id}."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rich.console import Console

from . import db
from .agents import PROVIDER_KEYS
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
    if not replay and not any(os.environ.get(k) for k in PROVIDER_KEYS):
        raise HTTPException(400, "no model API key is set; pass `replay` to replay a recorded run")
    run_dir = new_run_dir(body.requirement, None)
    run_id, record = await db.start_run(app.state.engine, body.requirement, body.scope, "full-auto", str(run_dir))
    task = asyncio.create_task(execute(body.requirement, repo, body.scope, "full-auto", ask=_no_terminal, answers=body.answers,
                                       replay=replay, run_dir=run_dir, record=record, console=Console(record=True, quiet=True)))
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
