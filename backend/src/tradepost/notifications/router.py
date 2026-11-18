import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from tradepost.db import Database
from tradepost.deps import DbSession
from tradepost.errors import Unauthorized
from tradepost.identity.sessions import CurrentUser, load_principal
from tradepost.notifications.models import Notification
from tradepost.notifications.service import Broker

router = APIRouter(prefix="/api", tags=["notifications"])

HEARTBEAT_SECONDS = 20
# Streams end after a while and the browser reconnects (EventSource does it on its own). This
# bounds how long a proxy or a sleeping instance can hold a dead connection.
STREAM_SECONDS = 300


class NotificationOut(BaseModel):
    id: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    unread: int
    next_before: int | None


class MarkReadIn(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=200)
    all: bool = False


def _out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=n.id, kind=n.kind, payload=n.payload, created_at=n.created_at, read_at=n.read_at
    )


async def _unread(db: DbSession, user_id: Any) -> int:
    return int(
        (
            await db.execute(
                select(func.count(Notification.id)).where(
                    Notification.user_id == user_id, Notification.read_at.is_(None)
                )
            )
        ).scalar_one()
    )


@router.get("/notifications")
async def list_notifications(
    user: CurrentUser,
    db: DbSession,
    before: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> NotificationPage:
    stmt = select(Notification).where(Notification.user_id == user.id)
    if before is not None:
        stmt = stmt.where(Notification.id < before)
    rows = list(
        (await db.execute(stmt.order_by(Notification.id.desc()).limit(limit + 1))).scalars()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return NotificationPage(
        items=[_out(n) for n in rows],
        unread=await _unread(db, user.id),
        next_before=rows[-1].id if more and rows else None,
    )


@router.post("/notifications/read")
async def mark_read(body: MarkReadIn, user: CurrentUser, db: DbSession) -> dict[str, int]:
    stmt = update(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None)
    )
    if not body.all:
        stmt = stmt.where(Notification.id.in_(body.ids or []))
    await db.execute(stmt.values(read_at=datetime.now(UTC)))
    return {"unread": await _unread(db, user.id)}


def _sse(event: str, data: Any, event_id: int | None = None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, default=str, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"


@router.get("/stream")
async def stream(
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Server-sent events: new notifications as they happen, plus the unread count.

    Authentication uses a short session instead of the request-scoped one: a stream lives for
    minutes, and holding a pooled connection (and a transaction) that long would starve the
    pool. Every read below opens and closes its own session."""
    database: Database = request.app.state.db
    broker: Broker = request.app.state.broker
    async with database.sessionmaker() as db:
        principal = await load_principal(request, db)
        await db.commit()
    if principal is None:
        raise Unauthorized("Sign in to continue.")
    user_id = principal.user.id
    try:
        cursor = int(last_event_id) if last_event_id else None
    except ValueError:
        cursor = None

    async def read_new(after: int | None) -> tuple[list[Notification], int]:
        async with database.sessionmaker() as db:
            if after is None:
                newest = (
                    await db.execute(
                        select(func.coalesce(func.max(Notification.id), 0)).where(
                            Notification.user_id == user_id
                        )
                    )
                ).scalar_one()
                return [], int(newest)
            rows = list(
                (
                    await db.execute(
                        select(Notification)
                        .where(Notification.user_id == user_id, Notification.id > after)
                        .order_by(Notification.id)
                        .limit(50)
                    )
                ).scalars()
            )
            return rows, rows[-1].id if rows else after

    async def events() -> AsyncIterator[str]:
        nonlocal cursor
        wake = broker.subscribe(user_id)
        started = time.monotonic()
        try:
            yield "retry: 3000\n\n"
            rows, cursor = await read_new(cursor)
            async with database.sessionmaker() as db:
                yield _sse("unread", {"count": await _unread(db, user_id)})
            for n in rows:
                yield _sse("notification", _out(n).model_dump(), n.id)
            while time.monotonic() - started < STREAM_SECONDS:
                if await request.is_disconnected():
                    return
                try:
                    await asyncio.wait_for(wake.wait(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                else:
                    # Something changed for this user (a message, a read mark): open chat
                    # screens refetch. Chat messages do not always create a notification.
                    yield _sse("wake", {})
                wake.clear()
                rows, cursor = await read_new(cursor)
                if rows:
                    for n in rows:
                        yield _sse("notification", _out(n).model_dump(), n.id)
                    async with database.sessionmaker() as db:
                        yield _sse("unread", {"count": await _unread(db, user_id)})
        finally:
            broker.unsubscribe(user_id, wake)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
