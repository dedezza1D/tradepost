"""Request metrics and error capture.

The middleware counts requests in memory, per minute and route; a job writes the counts to
PostgreSQL every tick. Nothing is sent to an outside service, and a lost minute (the process
restarting) only loses that minute of statistics.
"""

import hashlib
import re
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.db import Database
from tradepost.logs import get_logger, safe_message, safe_traceback

log = get_logger(__name__)

# Upper bounds (ms) of the latency buckets; the last bucket is open-ended.
BUCKETS_MS = (25, 50, 100, 250, 500, 1000, 2500)

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NUMBER = re.compile(r"/\d+(?=/|$)")
# Long-lived or noisy paths that would distort latency.
_SKIP = ("/api/stream", "/healthz", "/media/")


def route_of(path: str) -> str:
    """The route template: ids replaced, so /api/listings/<uuid> counts as one route."""
    path = _UUID.sub("{id}", path)
    path = _NUMBER.sub("/{n}", path)
    return path[:160]


def bucket_of(ms: float) -> int:
    for i, bound in enumerate(BUCKETS_MS):
        if ms <= bound:
            return i
    return len(BUCKETS_MS)


@dataclass
class _Cell:
    count: int = 0
    errors_5xx: int = 0
    errors_4xx: int = 0
    total_ms: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * (len(BUCKETS_MS) + 1))


class Collector:
    def __init__(self) -> None:
        self._cells: dict[tuple[int, str, str], _Cell] = defaultdict(_Cell)

    def observe(self, method: str, path: str, status: int, ms: float) -> None:
        if path.startswith(_SKIP) or not path.startswith("/api/"):
            return
        minute = int(time.time()) // 60 * 60
        cell = self._cells[(minute, method, route_of(path))]
        cell.count += 1
        cell.total_ms += ms
        cell.buckets[bucket_of(ms)] += 1
        if status >= 500:
            cell.errors_5xx += 1
        elif status >= 400:
            cell.errors_4xx += 1

    def drain(self) -> dict[tuple[int, str, str], _Cell]:
        cells, self._cells = self._cells, defaultdict(_Cell)
        return dict(cells)


collector = Collector()

_UPSERT = text(
    """
    INSERT INTO request_stats
        (minute, method, route, count, errors_5xx, errors_4xx, total_ms, buckets)
    VALUES (:minute, :method, :route, :count, :e5, :e4, :total, :buckets)
    ON CONFLICT (minute, method, route) DO UPDATE SET
        count = request_stats.count + excluded.count,
        errors_5xx = request_stats.errors_5xx + excluded.errors_5xx,
        errors_4xx = request_stats.errors_4xx + excluded.errors_4xx,
        total_ms = request_stats.total_ms + excluded.total_ms,
        buckets = ARRAY(
            SELECT a + b FROM unnest(request_stats.buckets, excluded.buckets) AS t(a, b))
    """
)


async def flush(db: AsyncSession, source: Collector | None = None) -> int:
    """Job: write the counted minutes. Returns how many route-minutes were written."""
    cells = (source or collector).drain()
    for (minute, method, route), c in cells.items():
        await db.execute(
            _UPSERT,
            {
                "minute": datetime.fromtimestamp(minute, UTC),
                "method": method,
                "route": route,
                "count": c.count,
                "e5": c.errors_5xx,
                "e4": c.errors_4xx,
                "total": c.total_ms,
                "buckets": c.buckets,
            },
        )
    return len(cells)


async def purge(db: AsyncSession, days: int = 7, error_days: int = 30) -> int:
    """Request statistics after ``days``; error groups not seen for ``error_days``."""
    stats = await db.execute(
        text("DELETE FROM request_stats WHERE minute < now() - make_interval(days => :d)"),
        {"d": days},
    )
    errors = await db.execute(
        text("DELETE FROM error_events WHERE last_seen < now() - make_interval(days => :d)"),
        {"d": error_days},
    )
    return int(stats.rowcount or 0) + int(errors.rowcount or 0)  # type: ignore[attr-defined]


# --- errors ------------------------------------------------------------------------------------

_RECORD = text(
    """
    INSERT INTO error_events
        (fingerprint, kind, route, message, traceback, count, last_request_id)
    VALUES (:fp, :kind, :route, :message, :tb, 1, :rid)
    ON CONFLICT (fingerprint) DO UPDATE SET
        count = error_events.count + 1,
        last_seen = now(),
        message = excluded.message,
        traceback = excluded.traceback,
        last_request_id = excluded.last_request_id
    """
)


def fingerprint(exc: BaseException, route: str) -> str:
    """Same bug, same group: exception type, route and the deepest frame in our own code.
    Messages are left out, since they often carry ids that differ every time."""
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "tradepost" in f.filename]
    where = f"{ours[-1].filename.rsplit('tradepost', 1)[-1]}:{ours[-1].name}" if ours else ""
    raw = f"{type(exc).__name__}|{route}|{where}"
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


async def record_error(
    database: Database, exc: BaseException, route: str, request_id: str | None
) -> None:
    """Store an unhandled error in its own transaction. Never raises: recording an error must
    not hide the original one."""
    try:
        tb = safe_traceback(exc)[-4000:]
        async with database.sessionmaker() as db, db.begin():
            await db.execute(
                _RECORD,
                {
                    "fp": fingerprint(exc, route),
                    "kind": type(exc).__name__[:120],
                    "route": route[:160],
                    "message": safe_message(exc)[:300],
                    "tb": tb,
                    "rid": request_id,
                },
            )
    except Exception:
        log.exception("error_record_failed")
