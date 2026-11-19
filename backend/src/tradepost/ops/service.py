"""Health of the running system for the admin operations page, alert rules, job tracking and
feature switches."""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.config import Settings
from tradepost.db import Database
from tradepost.identity.models import User
from tradepost.logs import get_logger
from tradepost.notifications.service import notify
from tradepost.ops.metrics import BUCKETS_MS
from tradepost.ops.models import AlertState, FeatureFlag, JobRun

log = get_logger(__name__)

# Alert thresholds. The free database plan has 1 GB.
ERROR_RATE = 0.05
ERROR_RATE_MIN_REQUESTS = 20
JOB_FAILURES = 3
JOB_STALLED = timedelta(minutes=10)
WEBHOOK_BACKLOG = timedelta(minutes=10)
PAYMENT_PENDING = timedelta(minutes=30)
DB_SIZE_BYTES = 800 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def percentile(buckets: list[int], q: float) -> float | None:
    """Upper bound of the bucket holding the q-th request (None without data). The open last
    bucket reports twice the largest bound, a deliberately rough "slow"."""
    total = sum(buckets)
    if total == 0:
        return None
    target = q * total
    seen = 0
    for i, n in enumerate(buckets):
        seen += n
        if seen >= target:
            return float(BUCKETS_MS[i]) if i < len(BUCKETS_MS) else float(BUCKETS_MS[-1] * 2)
    return float(BUCKETS_MS[-1] * 2)


def _add(a: list[int], b: list[int]) -> list[int]:
    return [x + y for x, y in zip(a, b, strict=True)] if a else list(b)


# --- jobs --------------------------------------------------------------------------------------

_JOB_OK = text(
    """
    INSERT INTO job_runs (name, last_started, last_ok_at, consecutive_failures, last_count,
                          last_duration_ms)
    VALUES (:name, :started, now(), 0, :count, :ms)
    ON CONFLICT (name) DO UPDATE SET last_started = excluded.last_started, last_ok_at = now(),
        consecutive_failures = 0, last_count = excluded.last_count,
        last_duration_ms = excluded.last_duration_ms
    """
)
_JOB_FAILED = text(
    """
    INSERT INTO job_runs (name, last_started, last_error_at, last_error, consecutive_failures,
                          last_count, last_duration_ms)
    VALUES (:name, :started, now(), :error, 1, 0, :ms)
    ON CONFLICT (name) DO UPDATE SET last_started = excluded.last_started, last_error_at = now(),
        last_error = excluded.last_error,
        consecutive_failures = job_runs.consecutive_failures + 1,
        last_duration_ms = excluded.last_duration_ms
    """
)


async def track_job(
    database: Database, name: str, run: Callable[[], Awaitable[int | None]]
) -> int | None:
    """Run one job and record how it went in its own transaction. Exceptions propagate after
    being recorded. Returns None when the job did not run (another instance holds it)."""
    started = _now()
    clock = time.perf_counter()
    try:
        count = await run()
    except Exception as exc:
        ms = (time.perf_counter() - clock) * 1000
        async with database.sessionmaker() as db, db.begin():
            await db.execute(
                _JOB_FAILED,
                {
                    "name": name,
                    "started": started,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                    "ms": ms,
                },
            )
        raise
    if count is not None:
        ms = (time.perf_counter() - clock) * 1000
        async with database.sessionmaker() as db, db.begin():
            await db.execute(_JOB_OK, {"name": name, "started": started, "count": count, "ms": ms})
    return count


# --- feature switches --------------------------------------------------------------------------


async def flag(db: AsyncSession, name: str, default: bool = True) -> bool:
    row = await db.get(FeatureFlag, name)
    return row.enabled if row is not None else default


async def set_flag(db: AsyncSession, name: str, enabled: bool) -> None:
    stmt = insert(FeatureFlag).values(name=name, enabled=enabled)
    await db.execute(stmt.on_conflict_do_update(index_elements=["name"], set_={"enabled": enabled}))


# --- snapshot and alerts -----------------------------------------------------------------------


@dataclass(frozen=True)
class Alert:
    key: str
    severity: str  # "warning" | "critical"


async def traffic(db: AsyncSession, minutes: int) -> dict[str, Any]:
    rows = (
        await db.execute(
            text(
                """
                SELECT minute, method, route, count, errors_5xx, errors_4xx, total_ms, buckets
                  FROM request_stats
                 WHERE minute > now() - make_interval(mins => :m)
                """
            ),
            {"m": minutes},
        )
    ).all()
    total_buckets: list[int] = []
    per_minute: dict[datetime, list[int]] = {}
    per_route: dict[tuple[str, str], dict[str, Any]] = {}
    requests = e5 = e4 = 0
    for minute, method, route, count, err5, err4, total_ms, buckets in rows:
        requests += count
        e5 += err5
        e4 += err4
        total_buckets = _add(total_buckets, buckets)
        m = per_minute.setdefault(minute, [0, 0])
        m[0] += count
        m[1] += err5
        r = per_route.setdefault(
            (method, route), {"count": 0, "errors_5xx": 0, "total_ms": 0.0, "buckets": []}
        )
        r["count"] += count
        r["errors_5xx"] += err5
        r["total_ms"] += total_ms
        r["buckets"] = _add(r["buckets"], buckets)
    routes = sorted(per_route.items(), key=lambda kv: kv[1]["count"], reverse=True)[:12]
    return {
        "requests": requests,
        "errors_5xx": e5,
        "errors_4xx": e4,
        "error_rate": round(e5 / requests, 4) if requests else 0.0,
        "p50_ms": percentile(total_buckets, 0.5),
        "p95_ms": percentile(total_buckets, 0.95),
        "per_minute": [
            {"minute": k, "count": v[0], "errors": v[1]} for k, v in sorted(per_minute.items())
        ],
        "routes": [
            {
                "method": method,
                "route": route,
                "count": r["count"],
                "errors_5xx": r["errors_5xx"],
                "avg_ms": round(r["total_ms"] / r["count"], 1) if r["count"] else None,
                "p95_ms": percentile(r["buckets"], 0.95),
            }
            for (method, route), r in routes
        ],
    }


async def payments(db: AsyncSession) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM checkouts WHERE status = 'pending'),
                  (SELECT count(*) FROM payment_attempts
                    WHERE status = 'pending' AND created_at < now() - CAST(:pending AS interval)),
                  (SELECT count(*) FROM payment_events WHERE processed_at IS NULL),
                  (SELECT extract(epoch FROM now() - min(received_at))
                     FROM payment_events WHERE processed_at IS NULL)
                """
            ),
            {"pending": PAYMENT_PENDING},
        )
    ).one()
    return {
        "pending_checkouts": int(row[0]),
        "stale_attempts": int(row[1]),
        "webhook_backlog": int(row[2]),
        "oldest_webhook_s": float(row[3]) if row[3] is not None else None,
    }


async def database_stats(db: AsyncSession) -> dict[str, Any]:
    size, connections = (
        await db.execute(
            text(
                "SELECT pg_database_size(current_database()),"
                " (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database())"
            )
        )
    ).one()
    return {"size_bytes": int(size), "connections": int(connections)}


async def jobs(db: AsyncSession) -> list[JobRun]:
    return list((await db.execute(select(JobRun).order_by(JobRun.name))).scalars())


async def evaluate(db: AsyncSession, settings: Settings) -> list[Alert]:
    alerts: list[Alert] = []
    recent = await traffic(db, 15)
    if recent["requests"] >= ERROR_RATE_MIN_REQUESTS and recent["error_rate"] > ERROR_RATE:
        alerts.append(Alert("error_rate", "critical"))
    now = _now()
    for job in await jobs(db):
        if job.consecutive_failures >= JOB_FAILURES:
            alerts.append(Alert(f"job_failing:{job.name}", "critical"))
        elif settings.run_worker and now - job.last_started > JOB_STALLED:
            alerts.append(Alert(f"job_stalled:{job.name}", "warning"))
    pay = await payments(db)
    if (
        pay["oldest_webhook_s"] is not None
        and pay["oldest_webhook_s"] > WEBHOOK_BACKLOG.total_seconds()
    ):
        alerts.append(Alert("webhook_backlog", "critical"))
    if pay["stale_attempts"]:
        alerts.append(Alert("payments_pending", "warning"))
    if (await database_stats(db))["size_bytes"] > DB_SIZE_BYTES:
        alerts.append(Alert("db_size", "warning"))
    from tradepost.ai.client import usage_today

    usage = await usage_today(db)
    if settings.ai_daily_budget_usd > 0 and usage["cost_usd"] >= 0.8 * settings.ai_daily_budget_usd:
        alerts.append(Alert("ai_budget", "warning"))
    return alerts


async def notify_alerts(db: AsyncSession, settings: Settings) -> int:
    """Job: tell admins (in the app) when an alert starts; forget alerts that cleared.
    Returns how many new alerts were announced."""
    firing = {a.key: a for a in await evaluate(db, settings)}
    known = {s.key: s for s in (await db.execute(select(AlertState))).scalars()}
    new = [a for key, a in firing.items() if key not in known or not known[key].active]
    for key, state in known.items():
        if key not in firing and state.active:
            state.active = False
    if not new:
        return 0
    admins = list((await db.execute(select(User.id).where(User.is_admin.is_(True)))).scalars())
    for alert in new:
        stmt = insert(AlertState).values(key=alert.key, active=True, since=_now())
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["key"], set_={"active": True, "since": _now()}
            )
        )
        for admin in admins:
            await notify(db, admin, "ops_alert", {"alert": alert.key, "severity": alert.severity})
        log.warning("ops_alert", alert=alert.key, severity=alert.severity)
    return len(new)


async def active_alerts(db: AsyncSession, settings: Settings) -> list[dict[str, Any]]:
    since = {s.key: s.since for s in (await db.execute(select(AlertState))).scalars() if s.active}
    return [
        {"key": a.key, "severity": a.severity, "since": since.get(a.key)}
        for a in await evaluate(db, settings)
    ]
