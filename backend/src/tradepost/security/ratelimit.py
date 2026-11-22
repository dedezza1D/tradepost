"""Fixed-window rate limiting backed by PostgreSQL.

The API runs as a single small instance, so a table with an atomic upsert is enough and avoids
another managed service. Each (key, window) pair is one row; old windows are purged by a job.
Counters are written in their own short transaction, so they survive a rollback of the request
that triggered them.
"""

import time
from collections.abc import Awaitable, Callable

from fastapi import Request
from sqlalchemy import BigInteger, Integer, String, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.config import get_settings
from tradepost.db import Base, Database
from tradepost.errors import TooManyRequests
from tradepost.security.client import client_ip


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)


_UPSERT = text(
    """
    INSERT INTO rate_limit_buckets (key, window_start, count)
    VALUES (:key, :window_start, 1)
    ON CONFLICT (key, window_start)
    DO UPDATE SET count = rate_limit_buckets.count + 1
    RETURNING count
    """
)


_COUNT = text(
    "SELECT count FROM rate_limit_buckets WHERE key = :key AND window_start = :window_start"
)


def _window(window_seconds: int) -> tuple[int, int]:
    """The current window's start and the seconds left until it ends."""
    now = int(time.time())
    window_start = now - now % window_seconds
    return window_start, window_start + window_seconds - now


async def record(database: Database, key: str, *, window_seconds: int) -> int:
    """Count one event for ``key`` in the current window and return the window's total."""
    window_start, _ = _window(window_seconds)
    async with database.sessionmaker() as db, db.begin():
        params = {"key": key[:200], "window_start": window_start}
        return int((await db.execute(_UPSERT, params)).scalar_one())


async def check(database: Database, key: str, *, limit: int, window_seconds: int) -> None:
    """Raise 429 when ``key`` already reached ``limit`` in the current window, without
    counting this call (for limits on outcomes, such as failed sign-ins)."""
    window_start, retry_after = _window(window_seconds)
    async with database.sessionmaker() as db:
        params = {"key": key[:200], "window_start": window_start}
        count = (await db.execute(_COUNT, params)).scalar_one_or_none() or 0
    if count >= limit * get_settings().rate_limit_scale:
        raise TooManyRequests(retry_after=retry_after)


async def hit(database: Database, key: str, *, limit: int, window_seconds: int) -> None:
    """Count one request for ``key``; raise 429 once the window's limit is exceeded."""
    count = await record(database, key, window_seconds=window_seconds)
    if count > limit * get_settings().rate_limit_scale:
        raise TooManyRequests(retry_after=_window(window_seconds)[1])


def rate_limit(name: str, *, limit: int, window_seconds: int) -> Callable[..., Awaitable[None]]:
    """Dependency limiting requests per client IP for one route group."""

    async def dependency(request: Request) -> None:
        await hit(
            request.app.state.db,
            f"{name}:ip:{client_ip(request)}",
            limit=limit,
            window_seconds=window_seconds,
        )

    return dependency


async def purge_old_buckets(db: AsyncSession, *, older_than_seconds: int = 3600) -> int:
    cutoff = int(time.time()) - older_than_seconds
    result = await db.execute(
        text("DELETE FROM rate_limit_buckets WHERE window_start < :cutoff"), {"cutoff": cutoff}
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
