"""Disputes and returns.

The buyer opens a dispute on a shipped order or within 7 days of delivery. Buyer, seller and the
platform talk in the dispute's thread. The seller refunds (all or part) or rejects; after a
rejection, or 72 hours without an answer, it goes to the platform, which decides. Refunds use the
same provider refund and ledger entries as a seller cancellation, at most once per order.

The seller's payout waits for the 7-day window after delivery and is held while a dispute is
open, so a refund never chases money already transferred.
"""

import enum
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base, new_id
from tradepost.deps import DbSession
from tradepost.errors import AppError, Conflict, Forbidden, NotFound
from tradepost.identity.models import User
from tradepost.identity.sessions import AdminUser, CurrentUser
from tradepost.notifications.service import notify
from tradepost.orders import service as orders
from tradepost.orders.models import LedgerEntry, LedgerKind, Order, OrderStatus, PaymentAttempt
from tradepost.payments.base import PaymentRegistry
from tradepost.security.ratelimit import rate_limit

OPEN_WINDOW = timedelta(days=7)
SELLER_ANSWER_WITHIN = timedelta(hours=72)


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class DisputeReason(enum.StrEnum):
    NOT_RECEIVED = "not_received"
    NOT_AS_DESCRIBED = "not_as_described"
    DAMAGED = "damaged"
    OTHER = "other"


class DisputeStatus(enum.StrEnum):
    OPEN = "open"  # waiting for the seller
    SELLER_REJECTED = "seller_rejected"  # the buyer may escalate
    ESCALATED = "escalated"  # waiting for the platform
    REFUNDED = "refunded"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


ACTIVE = (DisputeStatus.OPEN, DisputeStatus.SELLER_REJECTED, DisputeStatus.ESCALATED)


class Dispute(Base):
    __tablename__ = "disputes"
    __table_args__ = (Index("ix_disputes_status", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), unique=True, nullable=False
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reason: Mapped[DisputeReason] = mapped_column(_enum(DisputeReason, "dispute_reason"))
    status: Mapped[DisputeStatus] = mapped_column(
        _enum(DisputeStatus, "dispute_status"), nullable=False, default=DisputeStatus.OPEN
    )
    refund_cents: Mapped[int | None] = mapped_column(BigInteger)
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    resolved_by: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DisputeMessage(Base):
    __tablename__ = "dispute_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dispute_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("disputes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_role: Mapped[str] = mapped_column(String(10), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _role(dispute: Dispute, user: User) -> str:
    if user.id == dispute.buyer_id:
        return "buyer"
    if user.id == dispute.seller_id:
        return "seller"
    if user.is_admin:
        return "platform"
    raise NotFound("Dispute not found.")


async def _summary(db: AsyncSession, dispute: Dispute) -> dict[str, object]:
    order = await db.get(Order, dispute.order_id)
    assert order is not None
    return {**orders.order_summary(order), "dispute_id": str(dispute.id)}


async def open_dispute(
    db: AsyncSession, order_id: uuid.UUID, buyer: User, reason: DisputeReason, body: str
) -> Dispute:
    order = await orders.get_order(db, order_id, buyer)
    if order.buyer_id != buyer.id:
        raise Forbidden("Only the buyer opens a dispute.", code="buyer_only")
    late = order.status == OrderStatus.DELIVERED and (
        order.delivered_at is None or order.delivered_at < _now() - OPEN_WINDOW
    )
    if order.status not in (OrderStatus.SHIPPED, OrderStatus.DELIVERED) or late:
        raise Conflict(
            "Disputes open after shipping and up to 7 days after delivery.",
            code="dispute_window_closed",
        )
    if await db.scalar(select(Dispute.id).where(Dispute.order_id == order.id)) is not None:
        raise Conflict("This order already has a dispute.", code="dispute_exists")
    dispute = Dispute(
        order_id=order.id, buyer_id=order.buyer_id, seller_id=order.seller_id, reason=reason
    )
    db.add(dispute)
    await db.flush()
    db.add(DisputeMessage(dispute_id=dispute.id, author_role="buyer", body=body))
    await db.refresh(dispute)
    await notify(db, order.seller_id, "dispute_opened", await _summary(db, dispute))
    return dispute


async def get_dispute(db: AsyncSession, dispute_id: uuid.UUID, user: User) -> Dispute:
    dispute = await db.get(Dispute, dispute_id)
    if dispute is None:
        raise NotFound("Dispute not found.")
    _role(dispute, user)
    return dispute


async def _locked(db: AsyncSession, dispute: Dispute) -> Dispute:
    return (
        await db.execute(
            select(Dispute)
            .where(Dispute.id == dispute.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def refund(
    db: AsyncSession,
    registry: PaymentRegistry,
    dispute: Dispute,
    user: User,
    amount_cents: int | None,
    note: str | None,
) -> Dispute:
    """The seller (while it is with them) or the platform (once escalated) refunds the buyer."""
    dispute = await _locked(db, dispute)
    role = _role(dispute, user)
    allowed = (
        role == "seller" and dispute.status in (DisputeStatus.OPEN, DisputeStatus.SELLER_REJECTED)
    ) or (role == "platform" and dispute.status == DisputeStatus.ESCALATED)
    if not allowed:
        raise Conflict("This dispute cannot be refunded now.", code="dispute_closed")
    order = await orders._locked_order(db, dispute.order_id)
    paid_out = await db.scalar(
        select(LedgerEntry.id).where(
            LedgerEntry.order_id == order.id, LedgerEntry.kind == LedgerKind.PAYOUT
        )
    )
    if paid_out is not None:
        raise Conflict("The seller was already paid for this order.", code="already_paid_out")
    amount = amount_cents or order.total_cents
    if amount > order.total_cents:
        raise AppError("A refund cannot exceed what the buyer paid.", code="refund_too_large")
    attempt = await db.get(PaymentAttempt, order.checkout.paid_attempt_id)
    assert attempt is not None
    provider = registry.get(attempt.provider)
    # The idempotency key is per dispute: a retry never refunds twice.
    await provider.refund(attempt.provider_ref, amount, f"dispute-{dispute.id}")
    fee_back = order.platform_fee_cents * amount // order.total_cents
    await orders._ledger(db, order, LedgerKind.REFUND, -amount, attempt.provider_ref)
    await orders._ledger(db, order, LedgerKind.FEE_REVERSAL, fee_back, None)
    # Returned goods are not put back in stock: the seller relists them if they are sellable.
    if amount == order.total_cents:
        orders._transition(db, order, OrderStatus.REFUNDED, actor=role, note="dispute")
    dispute.status = DisputeStatus.REFUNDED
    dispute.refund_cents = amount
    dispute.resolution_note = note
    dispute.resolved_by = role
    dispute.resolved_at = _now()
    summary = {**await _summary(db, dispute), "amount_cents": amount}
    await notify(db, dispute.buyer_id, "dispute_refunded", summary)
    if role == "platform":
        await notify(db, dispute.seller_id, "dispute_refunded", summary)
    return dispute


async def reject(db: AsyncSession, dispute: Dispute, user: User, note: str) -> Dispute:
    dispute = await _locked(db, dispute)
    role = _role(dispute, user)
    if role == "seller" and dispute.status == DisputeStatus.OPEN:
        dispute.status = DisputeStatus.SELLER_REJECTED
        db.add(DisputeMessage(dispute_id=dispute.id, author_role="seller", body=note))
        await notify(db, dispute.buyer_id, "dispute_seller_rejected", await _summary(db, dispute))
        return dispute
    if role == "platform" and dispute.status == DisputeStatus.ESCALATED:
        dispute.status = DisputeStatus.REJECTED
        dispute.resolution_note = note
        dispute.resolved_by = role
        dispute.resolved_at = _now()
        for party in (dispute.buyer_id, dispute.seller_id):
            await notify(db, party, "dispute_rejected", await _summary(db, dispute))
        return dispute
    raise Conflict("This dispute cannot be rejected now.", code="dispute_closed")


async def escalate(db: AsyncSession, dispute: Dispute, user: User) -> Dispute:
    """The buyer asks the platform to decide: after the seller rejects, or once the seller has
    let 72 hours pass without an answer."""
    dispute = await _locked(db, dispute)
    if _role(dispute, user) != "buyer":
        raise Forbidden("Only the buyer escalates.", code="buyer_only")
    overdue = (
        dispute.status == DisputeStatus.OPEN and dispute.created_at < _now() - SELLER_ANSWER_WITHIN
    )
    if dispute.status != DisputeStatus.SELLER_REJECTED and not overdue:
        raise Conflict("The seller still has time to answer.", code="dispute_not_escalable")
    dispute.status = DisputeStatus.ESCALATED
    await notify(db, dispute.seller_id, "dispute_escalated", await _summary(db, dispute))
    return dispute


async def cancel(db: AsyncSession, dispute: Dispute, user: User) -> Dispute:
    dispute = await _locked(db, dispute)
    if _role(dispute, user) != "buyer":
        raise Forbidden("Only the buyer closes the dispute.", code="buyer_only")
    if dispute.status not in ACTIVE:
        raise Conflict("This dispute is already closed.", code="dispute_closed")
    dispute.status = DisputeStatus.CANCELLED
    dispute.resolved_by = "buyer"
    dispute.resolved_at = _now()
    await notify(db, dispute.seller_id, "dispute_cancelled", await _summary(db, dispute))
    return dispute


async def escalate_overdue(db: AsyncSession) -> int:
    """Job: disputes the seller left unanswered go to the platform."""
    rows = (
        await db.execute(
            update(Dispute)
            .where(
                Dispute.status == DisputeStatus.OPEN,
                Dispute.created_at < _now() - SELLER_ANSWER_WITHIN,
            )
            .values(status=DisputeStatus.ESCALATED)
            .returning(Dispute.id, Dispute.seller_id)
        )
    ).all()
    for dispute_id, seller_id in rows:
        dispute = await db.get(Dispute, dispute_id)
        if dispute is not None:
            await notify(db, seller_id, "dispute_escalated", await _summary(db, dispute))
    return len(rows)


# --- API ---------------------------------------------------------------------------------------

Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class OpenIn(BaseModel):
    reason: DisputeReason
    body: Body


class MessageIn(BaseModel):
    body: Body


class RefundIn(BaseModel):
    # Empty: refund everything the buyer paid.
    amount_cents: int | None = Field(default=None, gt=0, le=100_000_000)
    note: str | None = Field(default=None, max_length=500)


class RejectIn(BaseModel):
    note: Body


class DisputeMessageOut(BaseModel):
    id: int
    author_role: str
    body: str
    created_at: datetime


class DisputeOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    reason: DisputeReason
    status: DisputeStatus
    refund_cents: int | None
    resolution_note: str | None
    resolved_by: str | None
    created_at: datetime
    resolved_at: datetime | None
    order_total_cents: int
    my_role: str
    can_escalate: bool
    messages: list[DisputeMessageOut]


router = APIRouter(prefix="/api", tags=["disputes"])


def _payments(request: Request) -> PaymentRegistry:
    registry: PaymentRegistry = request.app.state.payments
    return registry


Payments = Annotated[PaymentRegistry, Depends(_payments)]


async def _out(db: AsyncSession, dispute: Dispute, user: User) -> DisputeOut:
    order = await db.get(Order, dispute.order_id)
    assert order is not None
    messages = (
        await db.execute(
            select(DisputeMessage)
            .where(DisputeMessage.dispute_id == dispute.id)
            .order_by(DisputeMessage.id)
        )
    ).scalars()
    role = _role(dispute, user)
    overdue = (
        dispute.status == DisputeStatus.OPEN and dispute.created_at < _now() - SELLER_ANSWER_WITHIN
    )
    return DisputeOut(
        id=dispute.id,
        order_id=dispute.order_id,
        reason=dispute.reason,
        status=dispute.status,
        refund_cents=dispute.refund_cents,
        resolution_note=dispute.resolution_note,
        resolved_by=dispute.resolved_by,
        created_at=dispute.created_at,
        resolved_at=dispute.resolved_at,
        order_total_cents=order.total_cents,
        my_role=role,
        can_escalate=role == "buyer"
        and (dispute.status == DisputeStatus.SELLER_REJECTED or overdue),
        messages=[
            DisputeMessageOut(
                id=m.id, author_role=m.author_role, body=m.body, created_at=m.created_at
            )
            for m in messages
        ],
    )


@router.post(
    "/orders/{order_id}/dispute",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("dispute", limit=10, window_seconds=3600))],
)
async def open_(order_id: uuid.UUID, body: OpenIn, user: CurrentUser, db: DbSession) -> DisputeOut:
    dispute = await open_dispute(db, order_id, user, body.reason, body.body)
    return await _out(db, dispute, user)


@router.get("/orders/{order_id}/dispute")
async def for_order(order_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DisputeOut:
    await orders.get_order(db, order_id, user)
    dispute = (
        await db.execute(select(Dispute).where(Dispute.order_id == order_id))
    ).scalar_one_or_none()
    if dispute is None:
        raise NotFound("No dispute for this order.")
    return await _out(db, dispute, user)


@router.get("/disputes/{dispute_id}")
async def get_(dispute_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DisputeOut:
    return await _out(db, await get_dispute(db, dispute_id, user), user)


@router.post(
    "/disputes/{dispute_id}/messages",
    dependencies=[Depends(rate_limit("dispute_message", limit=60, window_seconds=3600))],
)
async def post_message(
    dispute_id: uuid.UUID, body: MessageIn, user: CurrentUser, db: DbSession
) -> DisputeOut:
    dispute = await get_dispute(db, dispute_id, user)
    if dispute.status not in ACTIVE:
        raise Conflict("This dispute is closed.", code="dispute_closed")
    role = _role(dispute, user)
    db.add(DisputeMessage(dispute_id=dispute.id, author_role=role, body=body.body))
    summary = await _summary(db, dispute)
    for party, party_role in ((dispute.buyer_id, "buyer"), (dispute.seller_id, "seller")):
        if party_role != role:
            await notify(db, party, "dispute_message", summary)
    await db.flush()
    return await _out(db, dispute, user)


@router.post("/disputes/{dispute_id}/refund")
async def refund_(
    dispute_id: uuid.UUID, body: RefundIn, user: CurrentUser, db: DbSession, payments: Payments
) -> DisputeOut:
    dispute = await get_dispute(db, dispute_id, user)
    dispute = await refund(db, payments, dispute, user, body.amount_cents, body.note)
    return await _out(db, dispute, user)


@router.post("/disputes/{dispute_id}/reject")
async def reject_(
    dispute_id: uuid.UUID, body: RejectIn, user: CurrentUser, db: DbSession
) -> DisputeOut:
    dispute = await reject(db, await get_dispute(db, dispute_id, user), user, body.note)
    return await _out(db, dispute, user)


@router.post("/disputes/{dispute_id}/escalate")
async def escalate_(dispute_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DisputeOut:
    dispute = await escalate(db, await get_dispute(db, dispute_id, user), user)
    return await _out(db, dispute, user)


@router.post("/disputes/{dispute_id}/cancel")
async def cancel_(dispute_id: uuid.UUID, user: CurrentUser, db: DbSession) -> DisputeOut:
    dispute = await cancel(db, await get_dispute(db, dispute_id, user), user)
    return await _out(db, dispute, user)


class AdminDisputeOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    reason: DisputeReason
    status: DisputeStatus
    created_at: datetime
    order_total_cents: int
    title: str


@router.get("/admin/disputes")
async def escalated(admin: AdminUser, db: DbSession) -> list[AdminDisputeOut]:
    rows = (
        await db.execute(
            select(Dispute, Order)
            .join(Order, Order.id == Dispute.order_id)
            .where(Dispute.status == DisputeStatus.ESCALATED)
            .order_by(Dispute.created_at)
            .limit(100)
        )
    ).all()
    return [
        AdminDisputeOut(
            id=d.id,
            order_id=d.order_id,
            reason=d.reason,
            status=d.status,
            created_at=d.created_at,
            order_total_cents=o.total_cents,
            title=o.items[0].title if o.items else "",
        )
        for d, o in rows
    ]
