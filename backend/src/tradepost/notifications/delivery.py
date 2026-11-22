"""Notifications outside the app: Web Push to the person's devices and transactional e-mail.

Every notification is created by ``notify``, in the transaction of the event that caused it.
In that same transaction ``queue`` adds a delivery row per channel the person wants for that
kind of notice (an outbox), so a push or an e-mail exists exactly when the event committed and
is never sent for a rolled-back one. A job sends the due deliveries, retries failures with
backoff and drops subscriptions the browser has given up. A push is skipped once the person has
read the notice in the app, or when it is more than a day old.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base, new_id
from tradepost.deps import DbSession
from tradepost.errors import AppError
from tradepost.identity.models import Session as SessionRow
from tradepost.identity.sessions import CurrentPrincipal, CurrentUser
from tradepost.logs import get_logger
from tradepost.notifications import texts
from tradepost.notifications.email import EmailFailed, EmailSender, compose
from tradepost.notifications.webpush import PushFailed, PushGone, Subscription, WebPush
from tradepost.security.ratelimit import rate_limit

log = get_logger(__name__)

BATCH = 50
MAX_ATTEMPTS = 5
MAX_DEVICES = 10
STALE_PUSH = timedelta(days=1)
KEEP_DAYS = 30
# Push services the browsers use. The server posts to the endpoint a browser hands it, so any
# other host is refused: an endpoint must not turn the API into a proxy to arbitrary addresses.
PUSH_HOSTS = (
    ".googleapis.com",
    ".push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)


class PushSubscriptionRow(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(50), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL")
    )
    user_agent: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String(20), primary_key=True)
    push: Mapped[bool] = mapped_column(Boolean, nullable=False)
    email: Mapped[bool] = mapped_column(Boolean, nullable=False)


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("notification_id", "channel"),
        Index(
            "ix_notification_deliveries_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # push | email
    # pending -> sent | skipped (nothing to send it to, or no longer useful) | failed
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


async def queue(db: AsyncSession, notification_id: int, user_id: uuid.UUID, kind: str) -> None:
    """Add the deliveries this person wants for this notice, in the caller's transaction."""
    category = texts.CATEGORY_OF.get(kind)
    if category is None:
        return
    await db.execute(
        text(
            """
            WITH saved AS (
                SELECT push, email FROM notification_preferences
                 WHERE user_id = :uid AND category = :category
            )
            INSERT INTO notification_deliveries (notification_id, channel)
            SELECT CAST(:nid AS bigint), 'push'
             WHERE coalesce((SELECT push FROM saved), CAST(:push_default AS boolean))
               AND EXISTS (SELECT 1 FROM push_subscriptions WHERE user_id = :uid)
            UNION ALL
            SELECT CAST(:nid AS bigint), 'email'
             WHERE CAST(:email_kind AS boolean)
               AND coalesce((SELECT email FROM saved), CAST(:email_default AS boolean))
            """
        ),
        {
            "nid": notification_id,
            "uid": user_id,
            "category": category,
            "push_default": texts.PUSH_DEFAULT[category],
            "email_default": texts.EMAIL_DEFAULT[category],
            "email_kind": kind in texts.EMAIL_KINDS,
        },
    )


# --- sending -------------------------------------------------------------------------------------


def push_message(
    notification_id: int, kind: str, payload: dict[str, Any], locale: str
) -> dict[str, Any]:
    lang = texts.language(locale)
    return {
        "title": texts.title(kind, lang),
        "body": texts.sentence(kind, payload, lang),
        "url": texts.target(kind, payload),
        # One notice per conversation or order on the device: a new one replaces the old.
        "tag": str(
            payload.get("conversation_id") or payload.get("order_id") or f"n{notification_id}"
        ),
        "id": notification_id,
    }


async def _push_to_devices(
    db: AsyncSession, push: WebPush, user_id: uuid.UUID, message: dict[str, Any]
) -> tuple[int, int, str | None]:
    """(sent, failed, last error) over the devices whose session is still signed in;
    subscriptions the push service says are gone are removed."""
    rows = (
        (
            await db.execute(
                select(PushSubscriptionRow)
                .join(SessionRow, SessionRow.id == PushSubscriptionRow.session_id)
                .where(
                    PushSubscriptionRow.user_id == user_id,
                    SessionRow.revoked_at.is_(None),
                    SessionRow.expires_at > func.now(),
                )
            )
        )
        .scalars()
        .all()
    )
    sent = failed = 0
    error: str | None = None

    async def one(row: PushSubscriptionRow) -> None:
        nonlocal sent, failed, error
        try:
            await push.send(Subscription(row.endpoint, row.p256dh, row.auth), message)
        except PushGone:
            await db.delete(row)
        except PushFailed as exc:
            failed += 1
            error = str(exc)
        else:
            sent += 1
            row.last_used_at = func.now()

    for row in rows:
        await one(row)
    return sent, failed, error


async def send_due(
    db: AsyncSession, push: WebPush | None, email: EmailSender | None, origin: str
) -> int:
    """Job: send the due deliveries. Returns how many were sent."""
    rows = (
        await db.execute(
            text(
                """
                SELECT d.id, d.channel, d.attempts, n.id AS notification_id, n.kind, n.payload,
                       n.read_at, n.created_at, u.id AS user_id, u.email, u.display_name, u.locale
                  FROM notification_deliveries d
                  JOIN notifications n ON n.id = d.notification_id
                  JOIN users u ON u.id = n.user_id
                 WHERE d.status = 'pending' AND d.next_attempt_at <= now()
                 ORDER BY d.id
                 LIMIT :batch
                   FOR UPDATE OF d SKIP LOCKED
                """
            ),
            {"batch": BATCH},
        )
    ).all()
    now = (await db.execute(text("SELECT now()"))).scalar_one()
    done = 0
    for row in rows:
        outcome: str
        error: str | None = None
        if row.channel == "push":
            if push is None:
                outcome, error = "skipped", "push not configured"
            elif row.read_at is not None:
                outcome, error = "skipped", "read in the app"
            elif now - row.created_at > STALE_PUSH:
                outcome, error = "skipped", "too old"
            else:
                message = push_message(row.notification_id, row.kind, row.payload, row.locale)
                sent, failed, error = await _push_to_devices(db, push, row.user_id, message)
                if sent:
                    outcome, error = "sent", None
                elif failed:
                    outcome = "retry"
                else:
                    outcome, error = "skipped", "no device"
        else:
            if email is None or not email.enabled:
                outcome, error = "skipped", "e-mail not configured"
            else:
                try:
                    await email.send(
                        compose(
                            to=row.email,
                            name=row.display_name,
                            kind=row.kind,
                            payload=row.payload,
                            locale=row.locale,
                            origin=origin,
                        )
                    )
                except EmailFailed as exc:
                    outcome, error = "retry", str(exc)
                else:
                    outcome = "sent"
        await _record(db, row.id, row.attempts, outcome, error)
        done += outcome == "sent"
    return done


async def _record(
    db: AsyncSession, delivery_id: int, attempts: int, outcome: str, error: str | None
) -> None:
    if outcome == "retry":
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            outcome = "failed"
        else:
            await db.execute(
                text(
                    "UPDATE notification_deliveries SET attempts = :a, error = :e,"
                    " next_attempt_at = now() + make_interval(mins => :wait) WHERE id = :id"
                ),
                {
                    "a": attempts,
                    "e": (error or "")[:200],
                    "wait": int(2**attempts),
                    "id": delivery_id,
                },
            )
            return
    await db.execute(
        text(
            "UPDATE notification_deliveries SET status = :s, attempts = :a, error = :e,"
            " sent_at = CASE WHEN :sent THEN now() END WHERE id = :id"
        ),
        {
            "s": outcome,
            "sent": outcome == "sent",
            "a": attempts,
            "e": error[:200] if error else None,
            "id": delivery_id,
        },
    )


async def purge(db: AsyncSession) -> int:
    """Job: forget finished deliveries after a month."""
    result = await db.execute(
        text(
            "DELETE FROM notification_deliveries WHERE status <> 'pending'"
            " AND created_at < now() - make_interval(days => :days)"
        ),
        {"days": KEEP_DAYS},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- API -----------------------------------------------------------------------------------------

router = APIRouter(prefix="/api", tags=["notifications"])
_subscribe_limit = Depends(rate_limit("push_subscribe", limit=30, window_seconds=3600))
_test_limit = Depends(rate_limit("push_test", limit=5, window_seconds=600))


def push_service(request: Request) -> WebPush | None:
    push: WebPush | None = request.app.state.jobs.push
    return push


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=200)
    auth: str = Field(min_length=16, max_length=50)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def _known_push_service(cls, value: str) -> str:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not any(host.endswith(s) for s in PUSH_HOSTS):
            raise ValueError("not a browser push service")
        return value


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=1000)


class CategoryPreference(BaseModel):
    category: str
    push: bool
    email: bool
    # Whether this category has e-mails at all (only order and dispute notices do).
    email_possible: bool


class PreferencesOut(BaseModel):
    push_available: bool
    email_available: bool
    public_key: str | None
    devices: int
    categories: list[CategoryPreference]


class PreferenceIn(BaseModel):
    category: str
    push: bool
    email: bool

    @field_validator("category")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in texts.CATEGORIES:
            raise ValueError("unknown category")
        return value


async def _preferences(db: AsyncSession, request: Request, user_id: uuid.UUID) -> PreferencesOut:
    saved = {
        p.category: p
        for p in (
            await db.execute(
                select(NotificationPreference).where(NotificationPreference.user_id == user_id)
            )
        ).scalars()
    }
    devices = await db.scalar(
        select(func.count())
        .select_from(PushSubscriptionRow)
        .where(PushSubscriptionRow.user_id == user_id)
    )
    push = push_service(request)
    email: EmailSender | None = request.app.state.jobs.email
    with_email = {texts.CATEGORY_OF[k] for k in texts.EMAIL_KINDS}
    return PreferencesOut(
        push_available=push is not None,
        email_available=email is not None and email.enabled,
        public_key=push.public_key if push else None,
        devices=int(devices or 0),
        categories=[
            CategoryPreference(
                category=c,
                push=saved[c].push if c in saved else texts.PUSH_DEFAULT[c],
                email=(saved[c].email if c in saved else texts.EMAIL_DEFAULT[c])
                and c in with_email,
                email_possible=c in with_email,
            )
            for c in texts.CATEGORIES
        ],
    )


@router.get("/me/notification-preferences")
async def get_preferences(user: CurrentUser, db: DbSession, request: Request) -> PreferencesOut:
    return await _preferences(db, request, user.id)


@router.put("/me/notification-preferences")
async def set_preference(
    body: PreferenceIn, user: CurrentUser, db: DbSession, request: Request
) -> PreferencesOut:
    await db.execute(
        text(
            """
            INSERT INTO notification_preferences (user_id, category, push, email)
            VALUES (:uid, :category, :push, :email)
            ON CONFLICT (user_id, category) DO UPDATE SET push = :push, email = :email
            """
        ),
        {"uid": user.id, "category": body.category, "push": body.push, "email": body.email},
    )
    return await _preferences(db, request, user.id)


@router.post(
    "/me/push-subscriptions",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_subscribe_limit],
)
async def subscribe(
    body: SubscriptionIn, principal: CurrentPrincipal, db: DbSession, request: Request
) -> Response:
    if push_service(request) is None:
        raise AppError("Push notifications are not available.", code="push_unavailable")
    user = principal.user
    # The device follows the session that registered it: once that session ends (sign-out,
    # expiry), pushes stop, so a shared computer does not keep showing someone else's notices.
    # The same browser may have belonged to someone else who signed out: it is yours now.
    await db.execute(
        text(
            """
            INSERT INTO push_subscriptions
                (id, user_id, session_id, endpoint, p256dh, auth, user_agent)
            VALUES (:id, :uid, :sid, :endpoint, :p256dh, :auth, :agent)
            ON CONFLICT (endpoint) DO UPDATE
                SET user_id = :uid, session_id = :sid, p256dh = :p256dh, auth = :auth,
                    user_agent = :agent
            """
        ),
        {
            "id": new_id(),
            "uid": user.id,
            "sid": principal.session.id,
            "endpoint": body.endpoint,
            "p256dh": body.keys.p256dh,
            "auth": body.keys.auth,
            "agent": (request.headers.get("user-agent") or "")[:200] or None,
        },
    )
    # Bounded: the oldest devices beyond the limit stop receiving.
    await db.execute(
        text(
            """
            DELETE FROM push_subscriptions WHERE id IN (
                SELECT id FROM push_subscriptions WHERE user_id = :uid
                 ORDER BY created_at DESC OFFSET :keep)
            """
        ),
        {"uid": user.id, "keep": MAX_DEVICES},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/push-subscriptions/remove", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(body: EndpointIn, user: CurrentUser, db: DbSession) -> Response:
    await db.execute(
        delete(PushSubscriptionRow).where(
            PushSubscriptionRow.user_id == user.id, PushSubscriptionRow.endpoint == body.endpoint
        )
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class TestOut(BaseModel):
    sent: int
    failed: int


@router.post("/me/push-subscriptions/test", dependencies=[_test_limit])
async def test_push(user: CurrentUser, db: DbSession, request: Request) -> TestOut:
    """Send a test notice to this person's devices now."""
    push = push_service(request)
    if push is None:
        raise AppError("Push notifications are not available.", code="push_unavailable")
    lang = texts.language(user.locale)
    message = {
        "title": "tradepost",
        "body": texts.TEST_PUSH[lang],
        "url": "/me#notifications",
        "tag": "test",
    }
    sent, failed, _ = await _push_to_devices(db, push, user.id, message)
    return TestOut(sent=sent, failed=failed)
