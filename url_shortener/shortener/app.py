from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import AnyHttpUrl, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from .codes import new_code, valid_alias
from .db import Click, Link, create_schema, make_engine

MAX_CODE_ATTEMPTS = 5


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.engine = make_engine()
    app.state.sessions = async_sessionmaker(app.state.engine, expire_on_commit=False)
    await create_schema(app.state.engine)
    yield
    await app.state.engine.dispose()


app = FastAPI(title="url-shortener", lifespan=lifespan)


class LinkIn(BaseModel):
    url: AnyHttpUrl = Field(description="http(s) URL to shorten")
    alias: str | None = Field(default=None, description="Optional custom code, 3-32 of [A-Za-z0-9_-]")
    expires_at: datetime | None = None


class LinkOut(BaseModel):
    code: str
    short_url: str
    target_url: str
    expires_at: datetime | None


class DayCount(BaseModel):
    day: str
    clicks: int


class Stats(BaseModel):
    code: str
    target_url: str
    total_clicks: int
    clicks_by_day: list[DayCount]
    top_referrers: list[tuple[str, int]]


def _out(request: Request, link: Link) -> LinkOut:
    return LinkOut(code=link.code, short_url=str(request.base_url) + link.code, target_url=link.target_url, expires_at=link.expires_at)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.post("/links", response_model=LinkOut, status_code=201)
async def create_link(body: LinkIn, request: Request):
    target = str(body.url)
    if len(target) > 2048:
        raise HTTPException(422, "url longer than 2048 characters")
    if body.expires_at and body.expires_at <= datetime.now(UTC):
        raise HTTPException(422, "expires_at must be in the future")
    if body.alias is not None and not valid_alias(body.alias):
        raise HTTPException(422, "alias must be 3-32 characters of [A-Za-z0-9_-] and not reserved")

    attempts = 1 if body.alias else MAX_CODE_ATTEMPTS
    for _ in range(attempts):
        link = Link(code=body.alias or new_code(), target_url=target, expires_at=body.expires_at)
        try:
            async with request.app.state.sessions.begin() as s:
                s.add(link)
            return _out(request, link)
        except IntegrityError:
            if body.alias:
                raise HTTPException(409, "alias already taken")
    raise HTTPException(503, "could not allocate a unique code, retry")


async def _record_click(sessions, link_id: int, referrer: str | None, user_agent: str | None) -> None:
    async with sessions.begin() as s:
        s.add(Click(link_id=link_id, referrer=referrer, user_agent=user_agent))


@app.get("/links/{code}/stats", response_model=Stats)
async def stats(code: str, request: Request):
    async with request.app.state.sessions() as s:
        link = await s.scalar(select(Link).where(Link.code == code))
        if link is None:
            raise HTTPException(404, "unknown code")
        total = await s.scalar(select(func.count()).where(Click.link_id == link.id))
        day = func.date_trunc("day", Click.at)
        by_day = (await s.execute(select(day, func.count()).where(Click.link_id == link.id).group_by(day).order_by(day))).all()
        refs = (await s.execute(
            select(Click.referrer, func.count()).where(Click.link_id == link.id, Click.referrer.is_not(None))
            .group_by(Click.referrer).order_by(func.count().desc()).limit(5))).all()
    return Stats(code=code, target_url=link.target_url, total_clicks=total,
                 clicks_by_day=[DayCount(day=d.date().isoformat(), clicks=n) for d, n in by_day],
                 top_referrers=[(r, n) for r, n in refs])


@app.get("/{code}")
async def follow(code: str, request: Request, background: BackgroundTasks):
    async with request.app.state.sessions() as s:
        link = await s.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise HTTPException(404, "unknown code")
    if link.expires_at and link.expires_at <= datetime.now(UTC):
        raise HTTPException(410, "link expired")
    background.add_task(_record_click, request.app.state.sessions, link.id,
                        request.headers.get("referer"), request.headers.get("user-agent"))
    return RedirectResponse(link.target_url, status_code=307)
