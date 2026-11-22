"""Background work around payments: webhook inbox, reconciliation, expiry, payouts.

Every path that learns about a payment (webhook, reconciliation, expiry check, the buyer's
own request) ends in ``service.apply_status``, which is idempotent. So they can overlap freely.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.identity.models import User
from tradepost.logs import get_logger
from tradepost.notifications.service import notify
from tradepost.orders import service
from tradepost.orders.models import (
    AttemptStatus,
    Checkout,
    CheckoutStatus,
    LedgerEntry,
    LedgerKind,
    Order,
    OrderStatus,
    PaymentAttempt,
    PaymentEvent,
    SellerAccount,
)
from tradepost.payments.base import PaymentRegistry, PayoutProvider, WebhookNotice

log = get_logger(__name__)

BATCH = 20
MAX_EVENT_ATTEMPTS = 10
RECONCILE_AFTER = timedelta(seconds=60)
AUTO_DELIVER_AFTER = timedelta(days=14)


def _now() -> datetime:
    return datetime.now(UTC)


# --- webhook inbox -------------------------------------------------------------------------


async def record_event(db: AsyncSession, provider: str, notice: WebhookNotice) -> int | None:
    """Store a verified notification. Returns its id, or None when it was already received."""
    stmt = (
        insert(PaymentEvent)
        .values(
            provider=provider,
            event_id=notice.event_id[:200],
            event_type=notice.event_type[:80],
            provider_ref=notice.provider_ref,
        )
        .on_conflict_do_nothing(index_elements=["provider", "event_id"])
        .returning(PaymentEvent.id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def process_event(db: AsyncSession, registry: PaymentRegistry, event: PaymentEvent) -> None:
    event.attempts += 1
    if event.provider_ref:
        attempt = await service.find_attempt(db, event.provider, event.provider_ref)
        if attempt is not None:
            # The notification only names the payment; its state comes from the provider API.
            status = await registry.get(event.provider).get_status(event.provider_ref)
            await service.apply_status(db, registry, attempt, status)
    event.processed_at = _now()
    event.last_error = None


# Processed webhook events are only needed to recognise redeliveries, which providers stop
# within days. Payment state lives in attempts, orders and the ledger, which are kept.
EVENT_RETENTION = timedelta(days=90)


async def purge_events(db: AsyncSession, *, retention: timedelta = EVENT_RETENTION) -> int:
    result = await db.execute(
        delete(PaymentEvent).where(PaymentEvent.processed_at < _now() - retention)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def process_inbox(db: AsyncSession, registry: PaymentRegistry) -> int:
    events = (
        await db.execute(
            select(PaymentEvent)
            .where(PaymentEvent.processed_at.is_(None), PaymentEvent.attempts < MAX_EVENT_ATTEMPTS)
            .order_by(PaymentEvent.received_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    ).scalars()
    done = 0
    for event in events:
        try:
            async with db.begin_nested():
                await process_event(db, registry, event)
            done += 1
        except Exception as exc:  # retried on a later tick
            event.last_error = f"{type(exc).__name__}: {exc}"[:500]
            log.warning("webhook_event_failed", event=event.event_id, error=event.last_error)
    return done


# --- reconciliation and expiry -------------------------------------------------------------


async def reconcile_pending(db: AsyncSession, registry: PaymentRegistry) -> int:
    """Ask the provider about attempts that have been pending for a while. Covers webhooks that
    were lost or arrived while the API was asleep."""
    attempts = (
        await db.execute(
            select(PaymentAttempt)
            .join(Checkout, Checkout.id == PaymentAttempt.checkout_id)
            .where(
                PaymentAttempt.status == AttemptStatus.PENDING,
                PaymentAttempt.created_at < _now() - RECONCILE_AFTER,
                Checkout.status == CheckoutStatus.PENDING,
            )
            .order_by(PaymentAttempt.updated_at)
            .limit(BATCH)
        )
    ).scalars()
    changed = 0
    for attempt in attempts:
        status = await registry.get(attempt.provider).get_status(attempt.provider_ref)
        if status.status != attempt.status:
            await service.apply_status(db, registry, attempt, status)
            changed += 1
        else:
            attempt.updated_at = _now()  # rotate through the queue
    return changed


async def expire_due(db: AsyncSession, registry: PaymentRegistry) -> int:
    checkouts = (
        await db.execute(
            select(Checkout)
            .where(Checkout.status == CheckoutStatus.PENDING, Checkout.expires_at < _now())
            .order_by(Checkout.expires_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    ).scalars()
    count = 0
    for checkout in list(checkouts):
        closed = await service.close_unpaid(
            db,
            registry,
            checkout,
            final=CheckoutStatus.EXPIRED,
            note="payment not received in time",
        )
        count += closed.status == CheckoutStatus.EXPIRED
    return count


async def auto_deliver(db: AsyncSession) -> int:
    """Shipped orders the buyer never confirmed are considered delivered after a while, so the
    seller is not left waiting forever."""
    orders = (
        await db.execute(
            select(Order)
            .where(
                Order.status == OrderStatus.SHIPPED,
                Order.shipped_at < _now() - AUTO_DELIVER_AFTER,
            )
            .limit(BATCH)
            .with_for_update(skip_locked=True, of=Order)
        )
    ).scalars()
    count = 0
    for order in orders:
        service._transition(db, order, OrderStatus.DELIVERED, actor="system", note="auto")
        order.delivered_at = _now()
        await notify(db, order.seller_id, "order_delivered", service.order_summary(order))
        count += 1
    return count


# --- payouts -------------------------------------------------------------------------------


def payout_provider(registry: PaymentRegistry, name: str) -> PayoutProvider | None:
    provider = registry.get(name)
    return provider if hasattr(provider, "transfer") else None  # type: ignore[return-value]


async def run_payouts(
    db: AsyncSession, registry: PaymentRegistry, hold: timedelta = timedelta(0)
) -> int:
    """Pay sellers their share once the buyer has the item and the dispute window has passed
    (escrow-like), never while a dispute is open. The amount is the order's ledger balance, so a
    partial refund lowers it. Only for providers that can transfer to a seller account; for the
    others the ledger records what is owed."""
    from tradepost.disputes import ACTIVE, Dispute

    paid_out = exists().where(
        LedgerEntry.order_id == Order.id, LedgerEntry.kind == LedgerKind.PAYOUT
    )
    disputed = exists().where(Dispute.order_id == Order.id, Dispute.status.in_(ACTIVE))
    rows = (
        await db.execute(
            select(Order, Checkout, SellerAccount)
            .join(Checkout, Checkout.id == Order.checkout_id)
            .join(
                SellerAccount,
                and_(
                    SellerAccount.user_id == Order.seller_id,
                    SellerAccount.provider == Checkout.provider,
                ),
            )
            .where(
                Order.status == OrderStatus.DELIVERED,
                Order.delivered_at <= _now() - hold,
                ~paid_out,
                ~disputed,
            )
            .limit(BATCH)
            .with_for_update(skip_locked=True, of=Order)
        )
    ).all()
    count = 0
    for order, checkout, account in rows:
        provider = payout_provider(registry, checkout.provider)
        if provider is None:
            continue
        balance = int(
            await db.scalar(
                select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
                    LedgerEntry.order_id == order.id
                )
            )
            or 0
        )
        if balance <= 0:
            # Fully refunded: record a zero payout so the order is not picked again.
            await service._ledger(db, order, LedgerKind.PAYOUT, 0, None)
            continue
        attempt = await db.get(PaymentAttempt, checkout.paid_attempt_id)
        transfer_ref = await provider.transfer(
            account_id=account.provider_account_id,
            amount_cents=balance,
            currency=checkout.currency,
            charge_ref=attempt.charge_ref if attempt else None,
            group=str(checkout.id),
            idempotency_key=f"payout-{order.id}",
        )
        await service._ledger(db, order, LedgerKind.PAYOUT, -balance, transfer_ref)
        await notify(
            db,
            order.seller_id,
            "payout_sent",
            {**service.order_summary(order), "amount_cents": balance},
        )
        count += 1
    return count


async def ensure_seller_account(
    db: AsyncSession, registry: PaymentRegistry, provider_name: str, user: User
) -> SellerAccount:
    provider = payout_provider(registry, provider_name)
    if provider is None:
        from tradepost.errors import AppError

        raise AppError("This provider does not pay out to sellers.", code="no_payouts")
    account = (
        await db.execute(
            select(SellerAccount).where(
                SellerAccount.user_id == user.id, SellerAccount.provider == provider_name
            )
        )
    ).scalar_one_or_none()
    if account is None:
        account_id = await provider.create_seller_account(
            email=user.email, display_name=user.display_name
        )
        account = SellerAccount(
            id=uuid.uuid4(), user_id=user.id, provider=provider_name, provider_account_id=account_id
        )
        db.add(account)
        await db.flush()
    return account


# --- seller dashboard ----------------------------------------------------------------------


@dataclass(frozen=True)
class SellerSummary:
    gross_cents: int
    fees_cents: int
    refunded_cents: int
    paid_out_cents: int
    in_escrow_cents: int  # paid or shipped: released when the buyer confirms delivery
    to_receive_cents: int  # delivered, not yet transferred


async def seller_summary(db: AsyncSession, seller_id: uuid.UUID) -> SellerSummary:
    sums: dict[Any, int] = dict(
        (
            await db.execute(
                select(LedgerEntry.kind, func.coalesce(func.sum(LedgerEntry.amount_cents), 0))
                .where(LedgerEntry.seller_id == seller_id)
                .group_by(LedgerEntry.kind)
            )
        ).all()
    )
    net = Order.total_cents - Order.platform_fee_cents
    escrow = (
        await db.execute(
            select(func.coalesce(func.sum(net), 0)).where(
                Order.seller_id == seller_id,
                Order.status.in_([OrderStatus.PAID, OrderStatus.SHIPPED]),
            )
        )
    ).scalar_one()
    paid_out = exists().where(
        LedgerEntry.order_id == Order.id, LedgerEntry.kind == LedgerKind.PAYOUT
    )
    to_receive = (
        await db.execute(
            select(func.coalesce(func.sum(net), 0)).where(
                Order.seller_id == seller_id,
                Order.status == OrderStatus.DELIVERED,
                ~paid_out,
            )
        )
    ).scalar_one()
    return SellerSummary(
        gross_cents=int(sums.get(LedgerKind.SALE, 0)),
        fees_cents=-int(sums.get(LedgerKind.PLATFORM_FEE, 0))
        - int(sums.get(LedgerKind.FEE_REVERSAL, 0)),
        refunded_cents=-int(sums.get(LedgerKind.REFUND, 0)),
        paid_out_cents=-int(sums.get(LedgerKind.PAYOUT, 0)),
        in_escrow_cents=int(escrow),
        to_receive_cents=int(to_receive),
    )
