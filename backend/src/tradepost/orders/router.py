import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select

from tradepost.catalog.models import Listing, ListingPhoto
from tradepost.config import get_settings
from tradepost.deps import DbSession
from tradepost.errors import AppError, NotFound
from tradepost.identity.models import User
from tradepost.identity.sessions import CurrentUser
from tradepost.media.storage import Storage
from tradepost.orders import processing, service
from tradepost.orders.models import (
    AttemptStatus,
    CartItem,
    Checkout,
    CheckoutStatus,
    Order,
    OrderEvent,
    PaymentAttempt,
    PaymentEvent,
    SellerAccount,
)
from tradepost.orders.schemas import (
    AttemptOut,
    CartLineIn,
    CartLineOut,
    CartOut,
    CheckoutIn,
    CheckoutOut,
    OrderEventOut,
    OrderItemOut,
    OrderOut,
    PartyOut,
    PaymentIn,
    PayoutAccountIn,
    PayoutAccountOut,
    ProviderOut,
    SellerSummaryOut,
    ShipIn,
)
from tradepost.payments.base import CardData, PaymentRegistry
from tradepost.security.ratelimit import rate_limit

router = APIRouter(prefix="/api", tags=["orders"])

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
# Reading a pending checkout also asks the provider, at most this often, so the buyer sees the
# payment land within seconds instead of waiting for the next background tick.
_LIVE_CHECK_INTERVAL = timedelta(seconds=4)


def get_payments(request: Request) -> PaymentRegistry:
    registry: PaymentRegistry = request.app.state.payments
    return registry


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


Payments = Annotated[PaymentRegistry, Depends(get_payments)]
StorageDep = Annotated[Storage, Depends(get_storage)]


# --- presenters ----------------------------------------------------------------------------


async def _users(db: DbSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, User]:
    rows = (await db.execute(select(User).where(User.id.in_(ids)))).scalars()
    return {u.id: u for u in rows}


async def _orders_out(
    db: DbSession, storage: Storage, orders: list[Order], viewer: User, *, events: bool = False
) -> list[OrderOut]:
    users = await _users(db, {o.buyer_id for o in orders} | {o.seller_id for o in orders})
    from tradepost.reviews.service import reviewed_order_ids

    reviewed = await reviewed_order_ids(db, [o.id for o in orders])
    history: dict[uuid.UUID, list[OrderEventOut]] = {}
    if events and orders:
        rows = (
            await db.execute(
                select(OrderEvent)
                .where(OrderEvent.order_id.in_([o.id for o in orders]))
                .order_by(OrderEvent.id)
            )
        ).scalars()
        for e in rows:
            history.setdefault(e.order_id, []).append(
                OrderEventOut(
                    from_status=e.from_status,
                    to_status=e.to_status,
                    actor=e.actor,
                    note=e.note,
                    created_at=e.created_at,
                )
            )
    out = []
    for o in orders:
        is_seller = viewer.id == o.seller_id
        out.append(
            OrderOut(
                id=o.id,
                checkout_id=o.checkout_id,
                status=o.status,
                buyer=PartyOut(id=o.buyer_id, display_name=users[o.buyer_id].display_name),
                seller=PartyOut(id=o.seller_id, display_name=users[o.seller_id].display_name),
                items=[
                    OrderItemOut(
                        listing_id=i.listing_id,
                        title=i.title,
                        unit_price_cents=i.unit_price_cents,
                        quantity=i.quantity,
                        shipping_cents=i.shipping_cents,
                        thumb_url=storage.url(i.thumb_key) if i.thumb_key else None,
                    )
                    for i in o.items
                ],
                items_cents=o.items_cents,
                shipping_cents=o.shipping_cents,
                total_cents=o.total_cents,
                platform_fee_cents=o.platform_fee_cents if is_seller else None,
                seller_net_cents=o.seller_net_cents if is_seller else None,
                tracking_code=o.tracking_code,
                created_at=o.created_at,
                paid_at=o.paid_at,
                shipped_at=o.shipped_at,
                delivered_at=o.delivered_at,
                events=history.get(o.id, []),
                reviewed=o.id in reviewed,
            )
        )
    return out


async def _checkout_out(
    db: DbSession, storage: Storage, payments: PaymentRegistry, checkout: Checkout, buyer: User
) -> CheckoutOut:
    provider = payments.get(checkout.provider)
    return CheckoutOut(
        id=checkout.id,
        status=checkout.status,
        provider=checkout.provider,
        provider_config=provider.public_config(),
        total_cents=checkout.total_cents,
        currency=checkout.currency,
        expires_at=checkout.expires_at,
        paid_at=checkout.paid_at,
        orders=await _orders_out(db, storage, list(checkout.orders), buyer),
        attempts=[
            AttemptOut(
                id=a.id,
                method=a.method,
                status=a.status,
                status_detail=a.status_detail,
                # Only open attempts need their client data (QR code, client secret).
                client_data=a.client_data if a.status == AttemptStatus.PENDING else {},
                created_at=a.created_at,
            )
            for a in checkout.attempts
        ],
    )


# --- providers and cart --------------------------------------------------------------------


@router.get("/payments/providers")
async def list_providers(payments: Payments) -> list[ProviderOut]:
    return [
        ProviderOut(name=p.name, methods=list(p.methods), config=p.public_config())
        for p in payments.all()
    ]


@router.get("/cart")
async def get_cart(user: CurrentUser, db: DbSession, storage: StorageDep) -> CartOut:
    rows = (
        await db.execute(
            select(CartItem, Listing, User)
            .join(Listing, Listing.id == CartItem.listing_id)
            .join(User, User.id == Listing.seller_id)
            .where(CartItem.user_id == user.id)
            .order_by(CartItem.added_at)
        )
    ).all()
    thumbs = dict(
        (
            await db.execute(
                select(ListingPhoto.listing_id, ListingPhoto.key_thumb)
                .where(ListingPhoto.listing_id.in_([listing.id for _, listing, _ in rows]))
                .order_by(ListingPhoto.listing_id, ListingPhoto.position)
                .distinct(ListingPhoto.listing_id)
            )
        ).all()
    )
    items = [
        CartLineOut(
            listing_id=listing.id,
            title=listing.title,
            price_cents=listing.price_cents,
            shipping_cents=listing.shipping_cents,
            quantity=item.quantity,
            available=listing.stock if listing.status.value == "active" else 0,
            status=listing.status,
            thumb_url=storage.url(thumbs[listing.id]) if listing.id in thumbs else None,
            seller_id=seller.id,
            seller_name=seller.display_name,
        )
        for item, listing, seller in rows
    ]
    buyable = [i for i in items if i.available >= i.quantity]
    items_cents = sum(i.price_cents * i.quantity for i in buyable)
    shipping = sum(i.shipping_cents for i in buyable)
    return CartOut(
        items=items,
        items_cents=items_cents,
        shipping_cents=shipping,
        total_cents=items_cents + shipping,
    )


@router.put("/cart/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def set_cart_line(
    listing_id: uuid.UUID, body: CartLineIn, user: CurrentUser, db: DbSession
) -> None:
    await service.set_cart_item(db, user, listing_id, body.quantity)


@router.delete("/cart/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_cart_line(listing_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.set_cart_item(db, user, listing_id, 0)


# --- checkout ------------------------------------------------------------------------------


@router.post(
    "/checkouts",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("checkout", limit=30, window_seconds=3600))],
)
async def create_checkout(
    body: CheckoutIn,
    response: Response,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    payments: Payments,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> CheckoutOut:
    if not _IDEMPOTENCY_KEY.match(idempotency_key):
        raise AppError("Idempotency-Key must be 8-80 letters, digits, '-' or '_'.", code="bad_key")
    if body.from_cart:
        rows = (await db.execute(select(CartItem).where(CartItem.user_id == user.id))).scalars()
        lines = [service.Line(r.listing_id, r.quantity) for r in rows]
    else:
        lines = [service.Line(i.listing_id, i.quantity) for i in body.items or []]
    checkout, created = await service.create_checkout(
        db, get_settings(), payments, user, body.provider, lines, idempotency_key
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return await _checkout_out(db, storage, payments, checkout, user)


@router.get("/checkouts/{checkout_id}")
async def get_checkout(
    checkout_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    payments: Payments,
) -> CheckoutOut:
    checkout = await service.get_checkout_for_buyer(db, checkout_id, user)
    if checkout.status == CheckoutStatus.PENDING:
        now = datetime.now(UTC)
        provider = payments.get(checkout.provider)
        for attempt in checkout.attempts:
            if (
                attempt.status == AttemptStatus.PENDING
                and now - attempt.updated_at > _LIVE_CHECK_INTERVAL
            ):
                result = await provider.get_status(attempt.provider_ref)
                attempt.updated_at = now
                await service.apply_status(db, payments, attempt, result)
        await db.refresh(checkout, ["attempts", "orders"])
    return await _checkout_out(db, storage, payments, checkout, user)


@router.post(
    "/checkouts/{checkout_id}/payments",
    dependencies=[Depends(rate_limit("payment", limit=30, window_seconds=600))],
)
async def start_payment(
    checkout_id: uuid.UUID,
    body: PaymentIn,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    payments: Payments,
) -> CheckoutOut:
    checkout = await service.get_checkout_for_buyer(db, checkout_id, user)
    card = (
        CardData(
            token=body.card.token,
            payment_method_id=body.card.payment_method_id,
            installments=body.card.installments,
            payer_email=str(body.card.payer_email),
            issuer_id=body.card.issuer_id,
        )
        if body.card
        else None
    )
    await service.start_payment(db, payments, checkout, user, body.method, card)
    await db.refresh(checkout, ["attempts", "orders"])
    return await _checkout_out(db, storage, payments, checkout, user)


@router.post("/checkouts/{checkout_id}/cancel")
async def cancel_checkout(
    checkout_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    payments: Payments,
) -> CheckoutOut:
    checkout = await service.get_checkout_for_buyer(db, checkout_id, user)
    if checkout.status == CheckoutStatus.PENDING:
        checkout = await service.close_unpaid(
            db, payments, checkout, final=CheckoutStatus.CANCELLED, note="cancelled by buyer"
        )
    return await _checkout_out(db, storage, payments, checkout, user)


# --- orders --------------------------------------------------------------------------------


@router.get("/orders")
async def list_orders(
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    role: Annotated[Literal["buyer", "seller"], Query()] = "buyer",
) -> list[OrderOut]:
    column = Order.buyer_id if role == "buyer" else Order.seller_id
    orders = (
        await db.execute(
            select(Order).where(column == user.id).order_by(Order.created_at.desc()).limit(100)
        )
    ).scalars()
    return await _orders_out(db, storage, list(orders), user)


@router.get("/orders/{order_id}")
async def get_order(
    order_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> OrderOut:
    order = await service.get_order(db, order_id, user)
    return (await _orders_out(db, storage, [order], user, events=True))[0]


@router.post("/orders/{order_id}/ship")
async def ship_order(
    order_id: uuid.UUID, body: ShipIn, user: CurrentUser, db: DbSession, storage: StorageDep
) -> OrderOut:
    order = await service.get_order(db, order_id, user)
    order = await service.mark_shipped(db, order, user, body.tracking_code)
    return (await _orders_out(db, storage, [order], user, events=True))[0]


@router.post("/orders/{order_id}/deliver")
async def confirm_delivery(
    order_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> OrderOut:
    order = await service.get_order(db, order_id, user)
    order = await service.confirm_delivery(db, order, user)
    return (await _orders_out(db, storage, [order], user, events=True))[0]


@router.post("/orders/{order_id}/refund")
async def refund_order(
    order_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep, payments: Payments
) -> OrderOut:
    order = await service.get_order(db, order_id, user)
    order = await service.refund_order(db, payments, order, user)
    return (await _orders_out(db, storage, [order], user, events=True))[0]


# --- seller money --------------------------------------------------------------------------


@router.get("/me/sales/summary")
async def sales_summary(user: CurrentUser, db: DbSession) -> SellerSummaryOut:
    summary = await processing.seller_summary(db, user.id)
    has_account = (
        await db.execute(select(SellerAccount.id).where(SellerAccount.user_id == user.id))
    ).first() is not None
    return SellerSummaryOut(**summary.__dict__, payout_account=has_account)


@router.post(
    "/me/payout-account",
    dependencies=[Depends(rate_limit("payout_account", limit=10, window_seconds=3600))],
)
async def connect_payout_account(
    body: PayoutAccountIn, user: CurrentUser, db: DbSession, payments: Payments
) -> PayoutAccountOut:
    account = await processing.ensure_seller_account(db, payments, body.provider, user)
    provider = processing.payout_provider(payments, body.provider)
    assert provider is not None
    url = await provider.onboarding_link(
        account.provider_account_id, f"{get_settings().public_origin}/seller"
    )
    return PayoutAccountOut(provider=body.provider, connected=True, onboarding_url=url)


# --- webhooks ------------------------------------------------------------------------------


@router.post("/webhooks/{provider}", include_in_schema=False)
async def webhook(
    provider: str, request: Request, db: DbSession, payments: Payments
) -> dict[str, str]:
    if provider not in payments.names():
        raise NotFound("Unknown provider.")
    body = await request.body()
    if len(body) > 256 * 1024:
        raise AppError("Payload too large.", code="payload_too_large")
    notice = payments.get(provider).verify_webhook(
        {k.lower(): v for k, v in request.headers.items()}, dict(request.query_params), body
    )
    if notice is None:
        return {"status": "ignored"}
    event_id = await processing.record_event(db, provider, notice)
    if event_id is None:
        return {"status": "duplicate"}
    # Commit the inbox row first: whatever happens next, the event is not lost.
    await db.commit()
    event = await db.get(PaymentEvent, event_id)
    assert event is not None
    try:
        async with db.begin_nested():
            await processing.process_event(db, payments, event)
    except Exception as exc:  # left for the inbox job
        event.last_error = f"{type(exc).__name__}: {exc}"[:500]
    return {"status": "received"}


__all__ = ["PaymentAttempt", "router"]
