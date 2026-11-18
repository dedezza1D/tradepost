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


async def hit(database: Database, key: str, *, limit: int, window_seconds: int) -> None:
    """Count one request for ``key``; raise 429 once the window's limit is exceeded."""
    now = int(time.time())
    window_start = now - now % window_seconds
    async with database.sessionmaker() as db, db.begin():
        params = {"key": key[:200], "window_start": window_start}
        count = (await db.execute(_UPSERT, params)).scalar_one()
    if count > limit * get_settings().rate_limit_scale:
        raise TooManyRequests(retry_after=window_start + window_seconds - now)


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
