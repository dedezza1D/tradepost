"""Creating notifications and waking up the users' open streams.

``notify`` writes the row in the caller's transaction, so a notification exists exactly when the
event that caused it was committed. It also issues ``pg_notify``, which PostgreSQL delivers only
on commit: open streams of that user wake up and read the new rows.
"""

import asyncio
import contextlib
import uuid
from collections import defaultdict
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.logs import get_logger
from tradepost.notifications.models import Notification

log = get_logger(__name__)

CHANNEL = "tp_notifications"
# Ceiling per user; older ones are pruned by a job (keeps the free database small).
KEEP_PER_USER = 200


async def notify(db: AsyncSession, user_id: uuid.UUID, kind: str, payload: dict[str, Any]) -> None:
    db.add(Notification(user_id=user_id, kind=kind, payload=payload))
    await db.execute(text("SELECT pg_notify(:ch, :uid)"), {"ch": CHANNEL, "uid": str(user_id)})


async def prune(db: AsyncSession) -> int:
    result = await db.execute(
        text(
            """
            DELETE FROM notifications n
             USING (
                SELECT id FROM (
                    SELECT id, row_number() OVER (PARTITION BY user_id ORDER BY id DESC) AS rn
                      FROM notifications
                ) ranked WHERE rn > :keep
             ) old
             WHERE n.id = old.id
            """
        ),
        {"keep": KEEP_PER_USER},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


class Broker:
    """One LISTEN connection per process fans wake-ups out to the user's open streams.

    Only a wake-up travels through it; streams always read the rows from the table. If the
    listener is down, streams still poll on their heartbeat, so nothing is lost."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def subscribe(self, user_id: uuid.UUID) -> asyncio.Event:
        event = asyncio.Event()
        self._waiters[str(user_id)].add(event)
        return event

    def unsubscribe(self, user_id: uuid.UUID, event: asyncio.Event) -> None:
        waiters = self._waiters.get(str(user_id))
        if waiters is not None:
            waiters.discard(event)
            if not waiters:
                self._waiters.pop(str(user_id), None)

    def _wake(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        for event in self._waiters.get(payload, ()):
            event.set()

    async def _run(self) -> None:
        delay = 1.0
        while True:
            try:
                conn = await asyncpg.connect(self._dsn)
                await conn.add_listener(CHANNEL, self._wake)
                delay = 1.0
                try:
                    while not conn.is_closed():
                        await asyncio.sleep(15)
                        await conn.execute("SELECT 1")
                finally:
                    with contextlib.suppress(Exception):
                        await conn.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("notification_listener_down", retry_in=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)


def asyncpg_dsn(sqlalchemy_url: str) -> str:
    """asyncpg wants a plain ``postgresql://`` URL with ``ssl`` as a query option."""
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)
