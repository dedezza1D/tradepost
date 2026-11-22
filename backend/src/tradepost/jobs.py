"""Background maintenance loop, run inside the API process.

The free hosting tier has no separate worker, so periodic work runs here. A transaction-scoped
PostgreSQL advisory lock makes each task exclusive, so running several API instances (or a
dedicated worker later) never processes the same task twice at the same time.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.ai.client import AI
from tradepost.ai.client import purge_cache as purge_ai_cache
from tradepost.catalog.semantic import embed_pending as embed_listings
from tradepost.chat.service import expire_due as expire_offers
from tradepost.config import Settings
from tradepost.db import Database
from tradepost.disputes import escalate_overdue as escalate_disputes
from tradepost.fx import refresh as refresh_fx
from tradepost.logs import get_logger
from tradepost.notifications.service import prune as prune_notifications
from tradepost.ops.metrics import flush as flush_metrics
from tradepost.ops.metrics import purge as purge_metrics
from tradepost.ops.metrics import record_error
from tradepost.ops.service import notify_alerts, track_job
from tradepost.orders import processing
from tradepost.payments.base import PaymentRegistry
from tradepost.saved_searches import send_alerts as send_saved_search_alerts
from tradepost.security.ratelimit import purge_old_buckets

log = get_logger(__name__)

TICK_SECONDS = 30
_LOCK_ID = 7_301_001


@dataclass(frozen=True)
class JobContext:
    settings: Settings
    payments: PaymentRegistry
    ai: AI | None = None


async def _embed_listings(db: AsyncSession, ctx: JobContext) -> int:
    return await embed_listings(db, ctx.ai) if ctx.ai is not None else 0


Task = Callable[[AsyncSession, JobContext], Awaitable[int]]

TASKS: dict[str, Task] = {
    "purge_rate_limits": lambda db, _: purge_old_buckets(db),
    "payment_inbox": lambda db, ctx: processing.process_inbox(db, ctx.payments),
    "reconcile_payments": lambda db, ctx: processing.reconcile_pending(db, ctx.payments),
    "expire_checkouts": lambda db, ctx: processing.expire_due(db, ctx.payments),
    "auto_deliver": lambda db, _: processing.auto_deliver(db),
    "seller_payouts": lambda db, ctx: processing.run_payouts(
        db, ctx.payments, timedelta(hours=ctx.settings.payout_hold_hours)
    ),
    "prune_notifications": lambda db, _: prune_notifications(db),
    "expire_offers": lambda db, _: expire_offers(db),
    "escalate_disputes": lambda db, _: escalate_disputes(db),
    "flush_metrics": lambda db, _: flush_metrics(db),
    "purge_metrics": lambda db, _: purge_metrics(db),
    "purge_ai_cache": lambda db, _: purge_ai_cache(db),
    "ops_alerts": lambda db, ctx: notify_alerts(db, ctx.settings),
    "embed_listings": lambda db, ctx: _embed_listings(db, ctx),
    "saved_search_alerts": lambda db, _: send_saved_search_alerts(db),
    "refresh_fx": lambda db, _: refresh_fx(db),
}


async def run_tick(database: Database, ctx: JobContext) -> dict[str, int]:
    """Run every task once, each in its own transaction."""
    results: dict[str, int] = {}
    for name, task in TASKS.items():

        async def run_one(name: str = name, task: Task = task) -> int | None:
            async with database.sessionmaker() as db, db.begin():
                locked = (
                    await db.execute(
                        text("SELECT pg_try_advisory_xact_lock(:id, hashtext(:name))"),
                        {"id": _LOCK_ID, "name": name},
                    )
                ).scalar_one()
                return await task(db, ctx) if locked else None

        try:
            count = await track_job(database, name, run_one)
            if count is not None:
                results[name] = count
        except Exception as exc:
            # One failing task must not starve the others; it is retried next tick.
            log.exception("job_failed", job=name)
            await record_error(database, exc, f"job {name}", None)
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
