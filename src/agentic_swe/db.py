"""Append-only audit trail of every run: what each agent produced and every human/gate decision."""

import os
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .orchestrator import Record

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://localhost:55432/agentic_swe")


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    requirement: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="running")
    run_dir: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ponytail: create_all instead of migrations; add alembic when the schema first changes
async def connect(url: str = DATABASE_URL) -> AsyncEngine:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


async def start_run(engine: AsyncEngine, requirement: str, scope: str, mode: str, run_dir: str) -> tuple[int, Record]:
    Session = async_sessionmaker(engine)
    async with Session.begin() as s:
        run = Run(requirement=requirement, scope=scope, mode=mode, run_dir=run_dir)
        s.add(run)
        await s.flush()
        run_id = run.id

    async def record(kind: str, payload: dict) -> None:
        async with Session.begin() as s:
            s.add(Event(run_id=run_id, kind=kind, payload=payload, at=datetime.now(UTC)))
            if kind == "run_finished":
                (await s.get(Run, run_id)).status = payload["status"]

    return run_id, record


async def list_runs(engine: AsyncEngine) -> list[dict]:
    async with async_sessionmaker(engine)() as s:
        runs = (await s.scalars(select(Run).order_by(Run.id.desc()).limit(50))).all()
        return [_run(r) for r in runs]


async def get_run(engine: AsyncEngine, run_id: int) -> dict | None:
    async with async_sessionmaker(engine)() as s:
        run = await s.get(Run, run_id)
        if not run:
            return None
        events = (await s.scalars(select(Event).where(Event.run_id == run_id).order_by(Event.id))).all()
        return {**_run(run), "events": [{"kind": e.kind, "at": e.at.isoformat(), "payload": e.payload} for e in events]}


def _run(r: Run) -> dict:
    return {"id": r.id, "requirement": r.requirement, "scope": r.scope, "mode": r.mode, "status": r.status,
            "run_dir": r.run_dir, "created_at": r.created_at.isoformat()}
