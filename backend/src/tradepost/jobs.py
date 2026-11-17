"""Background maintenance loop, run inside the API process.

The free hosting tier has no separate worker, so periodic work runs here. A transaction-scoped
PostgreSQL advisory lock makes each tick exclusive, so running several API instances (or a
dedicated worker later) never processes the same tick twice.
"""

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.config import Settings
from tradepost.db import Database
from tradepost.logs import get_logger
from tradepost.security.ratelimit import purge_old_buckets

log = get_logger(__name__)

TICK_SECONDS = 30
_LOCK_ID = 7_301_001

Task = Callable[[AsyncSession, Settings], Awaitable[int]]


async def _purge_rate_limits(db: AsyncSession, _: Settings) -> int:
    return await purge_old_buckets(db)


_tasks: list[tuple[str, Task]] = [("purge_rate_limits", _purge_rate_limits)]


def register(name: str, task: Task) -> None:
    _tasks.append((name, task))


async def run_tick(database: Database, settings: Settings) -> dict[str, int]:
    """Run every registered task once, each in its own transaction."""
    results: dict[str, int] = {}
    for name, task in list(_tasks):
        try:
            async with database.sessionmaker() as db, db.begin():
                locked = (
                    await db.execute(
                        text("SELECT pg_try_advisory_xact_lock(:id, hashtext(:name))"),
                        {"id": _LOCK_ID, "name": name},
                    )
                ).scalar_one()
                if locked:
                    results[name] = await task(db, settings)
        except Exception:
            # One failing task must not starve the others; it is retried next tick.
            log.exception("job_failed", job=name)
    return results


async def run_worker(database: Database, settings: Settings) -> None:
    while True:
        try:
            results = await run_tick(database, settings)
            touched = {k: v for k, v in results.items() if v}
            if touched:
                log.info("jobs_tick", **touched)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("jobs_tick_failed")
        await asyncio.sleep(TICK_SECONDS)
