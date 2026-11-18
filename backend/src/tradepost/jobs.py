"""Background maintenance loop, run inside the API process.

The free hosting tier has no separate worker, so periodic work runs here. A transaction-scoped
PostgreSQL advisory lock makes each task exclusive, so running several API instances (or a
dedicated worker later) never processes the same task twice at the same time.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.config import Settings
from tradepost.db import Database
from tradepost.logs import get_logger
from tradepost.notifications.service import prune as prune_notifications
from tradepost.orders import processing
from tradepost.payments.base import PaymentRegistry
from tradepost.security.ratelimit import purge_old_buckets

log = get_logger(__name__)

TICK_SECONDS = 30
_LOCK_ID = 7_301_001


@dataclass(frozen=True)
class JobContext:
    settings: Settings
    payments: PaymentRegistry


Task = Callable[[AsyncSession, JobContext], Awaitable[int]]

TASKS: dict[str, Task] = {
    "purge_rate_limits": lambda db, _: purge_old_buckets(db),
    "payment_inbox": lambda db, ctx: processing.process_inbox(db, ctx.payments),
    "reconcile_payments": lambda db, ctx: processing.reconcile_pending(db, ctx.payments),
    "expire_checkouts": lambda db, ctx: processing.expire_due(db, ctx.payments),
    "auto_deliver": lambda db, _: processing.auto_deliver(db),
    "seller_payouts": lambda db, ctx: processing.run_payouts(db, ctx.payments),
    "prune_notifications": lambda db, _: prune_notifications(db),
}


async def run_tick(database: Database, ctx: JobContext) -> dict[str, int]:
    """Run every task once, each in its own transaction."""
    results: dict[str, int] = {}
    for name, task in TASKS.items():
        try:
            async with database.sessionmaker() as db, db.begin():
                locked = (
                    await db.execute(
                        text("SELECT pg_try_advisory_xact_lock(:id, hashtext(:name))"),
                        {"id": _LOCK_ID, "name": name},
                    )
                ).scalar_one()
                if locked:
                    results[name] = await task(db, ctx)
        except Exception:
            # One failing task must not starve the others; it is retried next tick.
            log.exception("job_failed", job=name)
    return results


async def run_worker(database: Database, ctx: JobContext) -> None:
    while True:
        try:
            results = await run_tick(database, ctx)
            touched = {k: v for k, v in results.items() if v}
            if touched:
                log.info("jobs_tick", **touched)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("jobs_tick_failed")
        await asyncio.sleep(TICK_SECONDS)
