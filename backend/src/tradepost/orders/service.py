"""Checkout and order rules: stock reservation, payment application, expiry, refunds.

Invariants this module keeps:

* Stock never goes negative and is never sold twice: reserving is one conditional UPDATE per
  listing (``stock >= q``), inside the transaction that creates the checkout.
* A checkout is paid at most once. Payment results are applied under a row lock on the
  checkout; a second successful attempt (double payment) or a payment that arrives after the
  reservation expired is refunded automatically.
* Applying the same payment result twice changes nothing (webhook redelivery, reconciliation
  racing a webhook). Ledger rows are unique per (order, kind).
"""

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.catalog.models import ListingPhoto
from tradepost.config import Settings
from tradepost.errors import AppError, Conflict, Forbidden, NotFound
from tradepost.identity.models import User
from tradepost.logs import get_logger
from tradepost.notifications.service import notify
from tradepost.orders.models import (
    AttemptStatus,
    CartItem,
    Checkout,
    CheckoutStatus,
    LedgerEntry,
    LedgerKind,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    PaymentAttempt,
    StockReservation,
)
from tradepost.payments.base import (
    CardData,
    CheckoutRef,
    Method,
    PaymentRegistry,
    StatusResult,
)

log = get_logger(__name__)

MAX_LINES = 20


def order_summary(order: Order) -> dict[str, object]:
    """Language-neutral facts a notification about an order needs."""
    items = list(order.items)
    return {
        "order_id": str(order.id),
        "title": items[0].title if items else "",
        "more_items": max(len(items) - 1, 0),
        "total_cents": order.total_cents,
    }


EXPIRY_GRACE = timedelta(minutes=10)
MAX_QUANTITY = 10


@dataclass(frozen=True)
class Line:
    listing_id: uuid.UUID
    quantity: int


class OutOfStock(Conflict):
    code = "out_of_stock"


def _now() -> datetime:
    return datetime.now(UTC)


def platform_fee(items_cents: int, bps: int) -> int:
    """Commission on the items only; shipping goes entirely to the seller. Rounded down."""
    return items_cents * bps // 10_000


# --- cart ---------------------------------------------------------------------------------


async def set_cart_item(db: AsyncSession, user: User, listing_id: uuid.UUID, quantity: int) -> None:
    if quantity <= 0:
        await db.execute(
            delete(CartItem).where(CartItem.user_id == user.id, CartItem.listing_id == listing_id)
        )
        return
    owner = (
        await db.execute(text("SELECT seller_id FROM listings WHERE id = :id"), {"id": listing_id})
    ).scalar_one_or_none()
    if owner is None:
        raise NotFound("Listing not found.")
    if owner == user.id:
        raise AppError("You cannot buy your own listing.", code="own_listing")
    count = (
        await db.execute(
            text("SELECT count(*) FROM cart_items WHERE user_id = :u AND listing_id <> :l"),
            {"u": user.id, "l": listing_id},
        )
    ).scalar_one()
    if count >= MAX_LINES:
        raise AppError(f"A cart holds at most {MAX_LINES} items.", code="cart_full")
    stmt = insert(CartItem).values(user_id=user.id, listing_id=listing_id, quantity=quantity)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[CartItem.user_id, CartItem.listing_id],
            set_={"quantity": quantity},
        )
    )


# --- checkout -----------------------------------------------------------------------------


def _request_hash(provider: str, lines: list[Line]) -> str:
    payload = json.dumps(
        {"p": provider, "l": [[str(x.listing_id), x.quantity] for x in lines]},
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _normalize(lines: list[Line]) -> list[Line]:
    merged: dict[uuid.UUID, int] = defaultdict(int)
    for line in lines:
        merged[line.listing_id] += line.quantity
    if not merged:
        raise AppError("Nothing to buy.", code="empty_checkout")
    if len(merged) > MAX_LINES:
        raise AppError(f"At most {MAX_LINES} different items per checkout.", code="too_many_items")
    if any(q < 1 or q > MAX_QUANTITY for q in merged.values()):
        raise AppError(f"Quantity must be between 1 and {MAX_QUANTITY}.", code="bad_quantity")
    # A fixed lock order (by listing id) keeps concurrent checkouts from deadlocking.
    return [Line(lid, merged[lid]) for lid in sorted(merged)]


_RESERVE = text(
    """
    UPDATE listings
       SET stock = stock - :q,
           version = version + 1,
           updated_at = now(),
           status = CASE WHEN stock - :q = 0 THEN 'sold_out'::listing_status ELSE status END
     WHERE id = :id AND status = 'active' AND stock >= :q AND seller_id <> :buyer
    RETURNING seller_id, title, price_cents, shipping_cents, currency
    """
)

_RELEASE = text(
    """
    UPDATE listings
       SET stock = stock + :q,
           version = version + 1,
           updated_at = now(),
           status = CASE WHEN status = 'sold_out' THEN 'active'::listing_status ELSE status END
     WHERE id = :id
    """
)


async def _explain_unavailable(db: AsyncSession, listing_id: uuid.UUID, buyer: User) -> AppError:
    row = (
        await db.execute(
            text("SELECT seller_id, status, stock FROM listings WHERE id = :id"),
            {"id": listing_id},
        )
    ).one_or_none()
    if row is None:
        return NotFound("Listing not found.", listing_id=str(listing_id))
    if row.seller_id == buyer.id:
        return AppError("You cannot buy your own listing.", code="own_listing")
    return OutOfStock(
        "Not enough stock for one of the items.",
        listing_id=str(listing_id),
        available=row.stock if row.status == "active" else 0,
    )


async def create_checkout(
    db: AsyncSession,
    settings: Settings,
    registry: PaymentRegistry,
    buyer: User,
    provider: str,
    lines: list[Line],
    idempotency_key: str,
) -> tuple[Checkout, bool]:
    """Reserve stock and open a checkout. Returns (checkout, created)."""
    registry.get(provider)
    lines = _normalize(lines)
    request_hash = _request_hash(provider, lines)

    existing = (
        await db.execute(
            select(Checkout).where(
                Checkout.buyer_id == buyer.id, Checkout.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise AppError(
                "This Idempotency-Key was already used for a different checkout.",
                code="idempotency_mismatch",
            )
        return existing, False

    by_seller: dict[uuid.UUID, list[tuple[Line, Any]]] = defaultdict(list)
    for line in lines:
        row = (
            await db.execute(
                _RESERVE, {"id": line.listing_id, "q": line.quantity, "buyer": buyer.id}
            )
        ).one_or_none()
        if row is None:
            # The request transaction rolls back, releasing whatever was reserved before.
            raise await _explain_unavailable(db, line.listing_id, buyer)
        by_seller[row.seller_id].append((line, row))

    checkout = Checkout(
        id=uuid.uuid4(),
        buyer_id=buyer.id,
        provider=provider,
        total_cents=0,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        expires_at=_now() + timedelta(minutes=settings.reservation_ttl_minutes),
    )
    thumbs = await _thumbs(db, [line.listing_id for line in lines])
    orders: list[Order] = []
    children: list[OrderItem | OrderEvent | StockReservation] = []
    total = 0
    for seller_id, rows in by_seller.items():
        items_cents = sum(r.price_cents * line.quantity for line, r in rows)
        shipping_cents = sum(r.shipping_cents for _, r in rows)
        order = Order(
            id=uuid.uuid4(),
            checkout_id=checkout.id,
            buyer_id=buyer.id,
            seller_id=seller_id,
            items_cents=items_cents,
            shipping_cents=shipping_cents,
            total_cents=items_cents + shipping_cents,
            platform_fee_cents=platform_fee(items_cents, settings.platform_fee_bps),
        )
        total += order.total_cents
        orders.append(order)
        for line, r in rows:
            children.append(
                OrderItem(
                    order_id=order.id,
                    listing_id=line.listing_id,
                    title=r.title,
                    unit_price_cents=r.price_cents,
                    quantity=line.quantity,
                    shipping_cents=r.shipping_cents,
                    thumb_key=thumbs.get(line.listing_id),
                )
            )
        children.append(
            OrderEvent(order_id=order.id, to_status=OrderStatus.AWAITING_PAYMENT, actor="buyer")
        )
    checkout.total_cents = total
    children += [
        StockReservation(
            checkout_id=checkout.id, listing_id=line.listing_id, quantity=line.quantity
        )
        for line in lines
    ]
    try:
        # Parents first: the unit of work does not order rows of unrelated mappers by their
        # foreign keys.
        db.add(checkout)
        await db.flush()
        db.add_all(orders)
        await db.flush()
        db.add_all(children)
        await db.flush()
    except IntegrityError as exc:
        if "uq_checkouts_buyer_id" not in str(exc.orig):
            raise
        # Same key sent twice at the same time: the other request won. The client retries and
        # gets that checkout.
        raise Conflict(
            "A checkout with this Idempotency-Key is being created. Retry.",
            code="idempotency_in_progress",
        ) from None
    await db.refresh(checkout, ["orders", "attempts"])
    for order in checkout.orders:
        await db.refresh(order, ["items"])
    return checkout, True


async def _thumbs(db: AsyncSession, listing_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    rows = await db.execute(
        select(ListingPhoto.listing_id, ListingPhoto.key_thumb)
        .where(ListingPhoto.listing_id.in_(listing_ids))
        .order_by(ListingPhoto.listing_id, ListingPhoto.position)
        .ext(distinct_on(ListingPhoto.listing_id))
    )
    return {lid: key for lid, key in rows}


async def get_checkout_for_buyer(db: AsyncSession, checkout_id: uuid.UUID, buyer: User) -> Checkout:
    checkout = await db.get(Checkout, checkout_id)
    if checkout is None or checkout.buyer_id != buyer.id:
        raise NotFound("Checkout not found.")
    return checkout


async def start_payment(
    db: AsyncSession,
    registry: PaymentRegistry,
    checkout: Checkout,
    buyer: User,
    method: Method,
    card: CardData | None,
) -> PaymentAttempt:
    if checkout.status != CheckoutStatus.PENDING or checkout.expires_at <= _now():
        raise Conflict("This checkout is no longer open for payment.", code="checkout_closed")
    provider = registry.get(checkout.provider)
    if method not in provider.methods:
        raise AppError("Payment method not available for this provider.", code="bad_method")
    if method == "card" and card is None and "element" not in provider.methods:
        raise AppError("Card details are required.", code="card_required")

    # Reuse an open attempt for the same method (a Stripe PaymentIntent handles retries itself;
    # a Pix QR code stays valid until it expires).
    for attempt in checkout.attempts:
        if attempt.method == method and attempt.status == AttemptStatus.PENDING and card is None:
            return attempt

    remaining = int((checkout.expires_at - _now()).total_seconds())
    result = await provider.start(
        CheckoutRef(
            checkout_id=str(checkout.id),
            amount_cents=checkout.total_cents,
            currency=checkout.currency,
            buyer_email=buyer.email,
            description=f"tradepost checkout {str(checkout.id)[:8]}",
            expires_in_seconds=max(remaining, 60),
        ),
        method,
        card,
    )
    attempt = PaymentAttempt(
        id=uuid.uuid4(),
        checkout_id=checkout.id,
        provider=provider.name,
        provider_ref=result.provider_ref,
        method=result.method,
        status=AttemptStatus.PENDING,
        amount_cents=checkout.total_cents,
        client_data=result.client_data,
        status_detail=result.status_detail,
    )
    try:
        async with db.begin_nested():
            db.add(attempt)
    except IntegrityError:
        # Two requests started the same payment at once and the provider's idempotency key
        # returned the same payment to both: keep the one already stored.
        existing = await find_attempt(db, provider.name, result.provider_ref)
        assert existing is not None
        return existing
    if result.status != AttemptStatus.PENDING:
        await apply_status(
            db,
            registry,
            attempt,
            StatusResult(result.status, result.charge_ref, result.status_detail),
        )
    await db.refresh(checkout, ["attempts", "orders"])
    return attempt


# --- applying provider results ---------------------------------------------------------------

_ATTEMPT_TRANSITIONS: dict[AttemptStatus, set[AttemptStatus]] = {
    AttemptStatus.PENDING: {
        AttemptStatus.SUCCEEDED,
        AttemptStatus.FAILED,
        AttemptStatus.CANCELLED,
    },
    # A cancel can race a payment the buyer just made; the payment wins.
    AttemptStatus.CANCELLED: {AttemptStatus.SUCCEEDED},
    AttemptStatus.FAILED: {AttemptStatus.SUCCEEDED},
    AttemptStatus.SUCCEEDED: {AttemptStatus.REFUNDED},
    AttemptStatus.REFUNDED: set(),
}


async def find_attempt(db: AsyncSession, provider: str, provider_ref: str) -> PaymentAttempt | None:
    return (
        await db.execute(
            select(PaymentAttempt).where(
                PaymentAttempt.provider == provider, PaymentAttempt.provider_ref == provider_ref
            )
        )
    ).scalar_one_or_none()


async def apply_status(
    db: AsyncSession, registry: PaymentRegistry, attempt: PaymentAttempt, result: StatusResult
) -> None:
    """Apply a provider-reported status to an attempt and its checkout. Idempotent."""
    # Lock order: checkout, then attempt. Every writer of either takes them in this order.
    checkout = (
        await db.execute(
            select(Checkout)
            .where(Checkout.id == attempt.checkout_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    attempt = (
        await db.execute(
            select(PaymentAttempt)
            .where(PaymentAttempt.id == attempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    new = result.status
    if new == attempt.status:
        if result.charge_ref and not attempt.charge_ref:
            attempt.charge_ref = result.charge_ref
        return
    if new not in _ATTEMPT_TRANSITIONS[attempt.status]:
        log.info("payment_transition_ignored", attempt=str(attempt.id), old=attempt.status, new=new)
        return

    attempt.status = new
    attempt.status_detail = result.status_detail
    if result.charge_ref:
        attempt.charge_ref = result.charge_ref
    log.info("payment_status", attempt=str(attempt.id), checkout=str(checkout.id), status=new)

    if new != AttemptStatus.SUCCEEDED:
        return
    if checkout.status == CheckoutStatus.PENDING:
        await _mark_paid(db, checkout, attempt)
        return
    if checkout.paid_attempt_id == attempt.id:
        return
    # Paid twice, or paid after the reservation was released: give the money back.
    reason = "double_payment" if checkout.status == CheckoutStatus.PAID else "late_payment"
    provider = registry.get(attempt.provider)
    await provider.refund(attempt.provider_ref, attempt.amount_cents, f"refund-{attempt.id}")
    attempt.status = AttemptStatus.REFUNDED
    attempt.status_detail = reason
    await notify(
        db,
        checkout.buyer_id,
        "payment_refunded",
        {"checkout_id": str(checkout.id), "amount_cents": attempt.amount_cents, "reason": reason},
    )
    log.warning("payment_refunded", attempt=str(attempt.id), reason=reason)


async def _mark_paid(db: AsyncSession, checkout: Checkout, attempt: PaymentAttempt) -> None:
    now = _now()
    checkout.status = CheckoutStatus.PAID
    checkout.paid_at = now
    checkout.paid_attempt_id = attempt.id
    await db.execute(
        update(StockReservation)
        .where(StockReservation.checkout_id == checkout.id, StockReservation.released_at.is_(None))
        .values(consumed_at=now)
    )
    await db.refresh(checkout, ["orders"])
    listing_ids: list[uuid.UUID] = []
    for order in checkout.orders:
        _transition(db, order, OrderStatus.PAID, actor="provider")
        order.paid_at = now
        await notify(db, order.seller_id, "order_paid", order_summary(order))
        await notify(db, order.buyer_id, "purchase_confirmed", order_summary(order))
        await _ledger(db, order, LedgerKind.SALE, order.total_cents, attempt.provider_ref)
        await _ledger(db, order, LedgerKind.PLATFORM_FEE, -order.platform_fee_cents, None)
        listing_ids += [item.listing_id for item in order.items]
    await db.execute(
        delete(CartItem).where(
            CartItem.user_id == checkout.buyer_id, CartItem.listing_id.in_(listing_ids)
        )
    )


async def _ledger(
    db: AsyncSession, order: Order, kind: LedgerKind, amount: int, provider_ref: str | None
) -> None:
    stmt = insert(LedgerEntry).values(
        order_id=order.id,
        seller_id=order.seller_id,
        kind=kind,
        amount_cents=amount,
        provider_ref=provider_ref,
    )
    await db.execute(stmt.on_conflict_do_nothing(index_elements=["order_id", "kind"]))


_ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.AWAITING_PAYMENT: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.SHIPPED, OrderStatus.REFUNDED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


def _transition(
    db: AsyncSession, order: Order, to: OrderStatus, *, actor: str, note: str | None = None
) -> None:
    if to not in _ORDER_TRANSITIONS[order.status]:
        raise Conflict(
            f"An order that is {order.status.value.replace('_', ' ')} cannot become "
            f"{to.value.replace('_', ' ')}.",
            code="invalid_transition",
        )
    db.add(
        OrderEvent(
            order_id=order.id, from_status=order.status, to_status=to, actor=actor, note=note
        )
    )
    order.status = to
    if to in (OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.DELIVERED):
        order.closed_at = _now()


# --- closing unpaid checkouts ----------------------------------------------------------------


async def close_unpaid(
    db: AsyncSession,
    registry: PaymentRegistry,
    checkout: Checkout,
    *,
    final: CheckoutStatus,
    note: str,
) -> Checkout:
    """Expire or cancel a pending checkout and return its stock.

    Open attempts are checked and cancelled at the provider first: a payment that already went
    through wins over the expiry."""
    provider = registry.get(checkout.provider)
    still_open = False
    for attempt in list(checkout.attempts):
        if attempt.status not in (AttemptStatus.PENDING, AttemptStatus.FAILED):
            continue
        status = await provider.get_status(attempt.provider_ref)
        if status.status == AttemptStatus.PENDING:
            status = await provider.cancel(attempt.provider_ref)
        await apply_status(db, registry, attempt, status)
        still_open = still_open or status.status == AttemptStatus.PENDING

    if (
        still_open
        and final == CheckoutStatus.EXPIRED
        and checkout.expires_at > _now() - EXPIRY_GRACE
    ):
        # A payment is being processed and can no longer be cancelled (a Pix under review, for
        # instance). Keep the stock a little longer; the next tick looks again. After the grace
        # period the checkout closes anyway and a late payment is refunded.
        return checkout

    checkout = (
        await db.execute(
            select(Checkout)
            .where(Checkout.id == checkout.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if checkout.status != CheckoutStatus.PENDING:
        return checkout

    reservations = (
        await db.execute(
            select(StockReservation).where(
                StockReservation.checkout_id == checkout.id,
                StockReservation.released_at.is_(None),
                StockReservation.consumed_at.is_(None),
            )
        )
    ).scalars()
    now = _now()
    for reservation in sorted(reservations, key=lambda r: r.listing_id):
        await db.execute(_RELEASE, {"id": reservation.listing_id, "q": reservation.quantity})
        reservation.released_at = now
    checkout.status = final
    checkout.closed_at = now
    await db.refresh(checkout, ["orders"])
    for order in checkout.orders:
        _transition(db, order, OrderStatus.CANCELLED, actor="system", note=note)
    return checkout


# --- order lifecycle after payment -----------------------------------------------------------


async def get_order(db: AsyncSession, order_id: uuid.UUID, user: User) -> Order:
    order = await db.get(Order, order_id)
    if order is None or user.id not in (order.buyer_id, order.seller_id):
        raise NotFound("Order not found.")
    return order


async def _locked_order(db: AsyncSession, order_id: uuid.UUID) -> Order:
    return (
        await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update(of=Order)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def mark_shipped(db: AsyncSession, order: Order, seller: User, tracking: str) -> Order:
    if order.seller_id != seller.id:
        raise Forbidden("Only the seller can ship this order.")
    order = await _locked_order(db, order.id)
    _transition(db, order, OrderStatus.SHIPPED, actor="seller", note=tracking)
    order.tracking_code = tracking
    await notify(
        db, order.buyer_id, "order_shipped", {**order_summary(order), "tracking_code": tracking}
    )
    order.shipped_at = _now()
    return order


async def confirm_delivery(db: AsyncSession, order: Order, buyer: User) -> Order:
    if order.buyer_id != buyer.id:
        raise Forbidden("Only the buyer can confirm delivery.")
    order = await _locked_order(db, order.id)
    _transition(db, order, OrderStatus.DELIVERED, actor="buyer")
    order.delivered_at = _now()
    await notify(db, order.seller_id, "order_delivered", order_summary(order))
    return order


async def refund_order(
    db: AsyncSession, registry: PaymentRegistry, order: Order, seller: User
) -> Order:
    """Seller cancels a paid order before shipping: the buyer gets the order total back."""
    if order.seller_id != seller.id:
        raise Forbidden("Only the seller can cancel this order.")
    order = await _locked_order(db, order.id)
    _transition(db, order, OrderStatus.REFUNDED, actor="seller", note="cancelled by seller")
    checkout = order.checkout
    attempt = await db.get(PaymentAttempt, checkout.paid_attempt_id)
    assert attempt is not None
    provider = registry.get(attempt.provider)
    await provider.refund(attempt.provider_ref, order.total_cents, f"refund-order-{order.id}")
    await _ledger(db, order, LedgerKind.REFUND, -order.total_cents, attempt.provider_ref)
    await _ledger(db, order, LedgerKind.FEE_REVERSAL, order.platform_fee_cents, None)
    await notify(db, order.buyer_id, "order_refunded", order_summary(order))
    for item in order.items:
        await db.execute(_RELEASE, {"id": item.listing_id, "q": item.quantity})
    return order
