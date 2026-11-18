"""Conversations, messages and offers.

Messages travel as plain POSTs and reach the other side through the notification stream
(server-sent events): every message wakes the recipient's open streams, and the web app then
reads the new messages. A WebSocket would add a second real-time channel, sticky connections
and its own reconnection logic for little gain at chat volumes; if the stream is unavailable
the app polls.

Offer transitions are single conditional UPDATEs (``... WHERE status = 'pending'``), so when
two requests race to accept, decline or counter the same offer, exactly one wins.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.catalog.models import Listing, ListingStatus
from tradepost.chat.detection import warning_for
from tradepost.chat.models import Conversation, Message, MessageKind, Offer, OfferStatus
from tradepost.errors import AppError, Conflict, Forbidden, NotFound
from tradepost.identity.models import User
from tradepost.notifications.service import CHANNEL, notify
from tradepost.orders.models import Checkout, CheckoutStatus
from tradepost.social.blocks import is_blocked

OFFER_TTL = timedelta(hours=24)
# After an offer is accepted the buyer has this long to pay the agreed price.
PAY_WINDOW = timedelta(hours=24)
MAX_PAGE = 50
CHAT_KINDS = (
    "new_message",
    "offer_received",
    "offer_countered",
    "offer_accepted",
    "offer_declined",
    "offer_expired",
)
_OPEN_LISTING = (ListingStatus.ACTIVE, ListingStatus.PAUSED, ListingStatus.SOLD_OUT)


def _now() -> datetime:
    return datetime.now(UTC)


async def wake(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Wake the user's open streams when this transaction commits."""
    await db.execute(text("SELECT pg_notify(:ch, :uid)"), {"ch": CHANNEL, "uid": str(user_id)})


# --- conversations ---------------------------------------------------------------------------


async def start(db: AsyncSession, user: User, listing_id: uuid.UUID) -> Conversation:
    """The buyer's conversation about a listing, created on first contact."""
    listing = await db.get(Listing, listing_id)
    if listing is None or listing.status not in _OPEN_LISTING:
        raise NotFound("Listing not found.")
    if listing.seller_id == user.id:
        raise AppError("This is your own listing.", code="own_listing_chat")
    if await is_blocked(db, user.id, listing.seller_id):
        raise NotFound("Listing not found.")
    existing = (
        await db.execute(
            select(Conversation).where(
                Conversation.listing_id == listing.id, Conversation.buyer_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    conversation = Conversation(
        listing_id=listing.id, buyer_id=user.id, seller_id=listing.seller_id
    )
    try:
        async with db.begin_nested():
            db.add(conversation)
    except IntegrityError:
        # The same buyer opened it twice at once: use the one that won.
        return (
            await db.execute(
                select(Conversation).where(
                    Conversation.listing_id == listing.id, Conversation.buyer_id == user.id
                )
            )
        ).scalar_one()
    await db.refresh(conversation)
    return conversation


async def get(db: AsyncSession, conversation_id: uuid.UUID, user: User) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.role_of(user.id) is None:
        # Same answer for "does not exist" and "not yours".
        raise NotFound("Conversation not found.")
    return conversation


async def unread_counts(
    db: AsyncSession, user: User, conversation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not conversation_ids:
        return {}
    rows = await db.execute(
        text(
            """
            SELECT c.id, count(m.id)
              FROM conversations c
              JOIN messages m ON m.conversation_id = c.id
             WHERE c.id = ANY(:ids)
               AND m.sender_id IS DISTINCT FROM :me
               AND m.created_at > COALESCE(
                     CASE WHEN c.buyer_id = :me THEN c.buyer_read_at ELSE c.seller_read_at END,
                     '-infinity')
             GROUP BY c.id
            """
        ),
        {"ids": conversation_ids, "me": user.id},
    )
    return {cid: int(n) for cid, n in rows}


async def unread_total(db: AsyncSession, user: User) -> int:
    """How many conversations have something the user has not read."""
    return int(
        (
            await db.execute(
                text(
                    """
                    SELECT count(*) FROM conversations c
                     WHERE (c.buyer_id = :me OR c.seller_id = :me)
                       AND EXISTS (
                           SELECT 1 FROM messages m
                            WHERE m.conversation_id = c.id
                              AND m.sender_id IS DISTINCT FROM :me
                              AND m.created_at > COALESCE(
                                    CASE WHEN c.buyer_id = :me
                                         THEN c.buyer_read_at ELSE c.seller_read_at END,
                                    '-infinity'))
                    """
                ),
                {"me": user.id},
            )
        ).scalar_one()
    )


async def inbox(
    db: AsyncSession, user: User, before: datetime | None, limit: int
) -> list[Conversation]:
    stmt = select(Conversation).where(
        (Conversation.buyer_id == user.id) | (Conversation.seller_id == user.id)
    )
    if before is not None:
        stmt = stmt.where(Conversation.last_message_at < before)
    return list(
        (
            await db.execute(stmt.order_by(Conversation.last_message_at.desc()).limit(limit))
        ).scalars()
    )


async def mark_read(db: AsyncSession, conversation: Conversation, user: User) -> None:
    now = _now()
    if conversation.role_of(user.id) == "buyer":
        conversation.buyer_read_at = now
    else:
        conversation.seller_read_at = now
    await db.execute(
        text(
            """
            UPDATE notifications SET read_at = now()
             WHERE user_id = :me AND read_at IS NULL AND kind = ANY(:kinds)
               AND payload->>'conversation_id' = :cid
            """
        ),
        {"me": user.id, "kinds": list(CHAT_KINDS), "cid": str(conversation.id)},
    )
    # The other side's "seen" mark changed.
    await wake(db, conversation.other(user.id))


# --- messages --------------------------------------------------------------------------------


async def _append(
    db: AsyncSession,
    conversation: Conversation,
    sender: uuid.UUID | None,
    kind: MessageKind,
    *,
    body: str | None = None,
    offer: Offer | None = None,
    warning: str | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        sender_id=sender,
        kind=kind,
        body=body,
        offer_id=offer.id if offer else None,
        warning=warning,
    )
    db.add(message)
    await db.flush()
    await db.refresh(message)
    conversation.last_message_at = message.created_at
    conversation.last_message_preview = (
        (body or "")[:140]
        if kind == MessageKind.TEXT
        else f"{kind}:{offer.amount_cents if offer else 0}"
    )
    # Writing counts as reading: your own message never shows up as unread for you.
    if sender == conversation.buyer_id:
        conversation.buyer_read_at = message.created_at
    elif sender == conversation.seller_id:
        conversation.seller_read_at = message.created_at
    return message


async def _tell(
    db: AsyncSession,
    conversation: Conversation,
    recipient: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
) -> None:
    """Notify the recipient. A run of chat messages yields one unread notification per
    conversation, not one per message; the stream is woken either way."""
    payload = {"conversation_id": str(conversation.id), **payload}
    if kind == "new_message":
        pending = (
            await db.execute(
                text(
                    """
                    SELECT 1 FROM notifications
                     WHERE user_id = :u AND kind = 'new_message' AND read_at IS NULL
                       AND payload->>'conversation_id' = :cid
                     LIMIT 1
                    """
                ),
                {"u": recipient, "cid": str(conversation.id)},
            )
        ).first()
        if pending is not None:
            await wake(db, recipient)
            return
    await notify(db, recipient, kind, payload)


async def _ensure_not_blocked(db: AsyncSession, conversation: Conversation) -> None:
    if await is_blocked(db, conversation.buyer_id, conversation.seller_id):
        raise Forbidden("You can no longer message this person.", code="blocked")


async def _listing_title(db: AsyncSession, conversation: Conversation) -> str:
    listing = await db.get(Listing, conversation.listing_id)
    return listing.title if listing else ""


async def send(db: AsyncSession, conversation: Conversation, user: User, body: str) -> Message:
    await _ensure_not_blocked(db, conversation)
    message = await _append(
        db, conversation, user.id, MessageKind.TEXT, body=body, warning=warning_for(body)
    )
    await _tell(
        db,
        conversation,
        conversation.other(user.id),
        "new_message",
        {"name": user.display_name, "title": await _listing_title(db, conversation)},
    )
    return message


async def messages(
    db: AsyncSession,
    conversation: Conversation,
    *,
    before: int | None = None,
    after: int | None = None,
    limit: int = MAX_PAGE,
) -> list[Message]:
    """A page of messages in chronological order: the newest page, older pages with
    ``before``, or what arrived since ``after``."""
    stmt = select(Message).where(Message.conversation_id == conversation.id)
    if after is not None:
        rows = await db.execute(stmt.where(Message.id > after).order_by(Message.id).limit(limit))
        return list(rows.scalars())
    if before is not None:
        stmt = stmt.where(Message.id < before)
    rows = await db.execute(stmt.order_by(Message.id.desc()).limit(limit))
    return list(reversed(list(rows.scalars())))


# --- offers ----------------------------------------------------------------------------------


async def _offer_listing(db: AsyncSession, conversation: Conversation, quantity: int) -> Listing:
    listing = await db.get(Listing, conversation.listing_id)
    if listing is None or listing.status != ListingStatus.ACTIVE:
        raise Conflict("This listing is not available.", code="listing_unavailable")
    if quantity > listing.stock:
        raise Conflict("Not enough stock for this quantity.", code="out_of_stock")
    return listing


def _check_amount(listing: Listing, amount_cents: int) -> None:
    if amount_cents > listing.price_cents:
        raise AppError("An offer cannot be above the listed price.", code="offer_above_price")


async def make_offer(
    db: AsyncSession, conversation: Conversation, user: User, amount_cents: int, quantity: int
) -> Offer:
    if conversation.role_of(user.id) != "buyer":
        raise Forbidden("Only the buyer opens an offer; the seller counters.", code="buyer_only")
    await _ensure_not_blocked(db, conversation)
    listing = await _offer_listing(db, conversation, quantity)
    _check_amount(listing, amount_cents)
    offer = await _insert_offer(db, conversation, user, amount_cents, quantity)
    await _append(db, conversation, user.id, MessageKind.OFFER, offer=offer)
    await _tell(
        db,
        conversation,
        conversation.seller_id,
        "offer_received",
        {"name": user.display_name, "title": listing.title, "amount_cents": amount_cents},
    )
    return offer


async def _insert_offer(
    db: AsyncSession, conversation: Conversation, user: User, amount_cents: int, quantity: int
) -> Offer:
    offer = Offer(
        conversation_id=conversation.id,
        proposer_id=user.id,
        amount_cents=amount_cents,
        quantity=quantity,
        expires_at=_now() + OFFER_TTL,
    )
    try:
        async with db.begin_nested():
            db.add(offer)
    except IntegrityError:
        raise Conflict(
            "There is already an open offer in this conversation.", code="offer_pending"
        ) from None
    return offer


async def get_offer(
    db: AsyncSession, offer_id: uuid.UUID, user: User
) -> tuple[Offer, Conversation]:
    offer = await db.get(Offer, offer_id)
    if offer is None:
        raise NotFound("Offer not found.")
    conversation = await get(db, offer.conversation_id, user)
    return offer, conversation


async def _transition(
    db: AsyncSession, offer: Offer, user: User, to: OfferStatus, *, by_proposer: bool
) -> None:
    """Move a pending offer to ``to`` if it is still pending and unexpired. Responding is for
    the other participant; withdrawing is for the proposer."""
    who = Offer.proposer_id == user.id if by_proposer else Offer.proposer_id != user.id
    changed = (
        await db.execute(
            update(Offer)
            .where(
                Offer.id == offer.id,
                Offer.status == OfferStatus.PENDING,
                Offer.expires_at > func.now(),
                who,
            )
            .values(status=to, responded_at=func.now())
            .returning(Offer.id)
        )
    ).first()
    if changed is not None:
        await db.refresh(offer)
        return
    await db.refresh(offer)
    if (offer.proposer_id == user.id) != by_proposer:
        raise Forbidden(
            "You made this offer." if not by_proposer else "This is not your offer.",
            code="not_your_turn",
        )
    if offer.status == OfferStatus.PENDING:  # still pending: it timed out
        raise Conflict("This offer has expired.", code="offer_expired")
    raise Conflict("This offer was already answered.", code="offer_closed", status=offer.status)


async def accept(db: AsyncSession, offer_id: uuid.UUID, user: User) -> Offer:
    offer, conversation = await get_offer(db, offer_id, user)
    await _ensure_not_blocked(db, conversation)
    await _transition(db, offer, user, OfferStatus.ACCEPTED, by_proposer=False)
    await _append(db, conversation, user.id, MessageKind.OFFER_ACCEPTED, offer=offer)
    await _tell(
        db,
        conversation,
        offer.proposer_id,
        "offer_accepted",
        {
            "name": user.display_name,
            "title": await _listing_title(db, conversation),
            "amount_cents": offer.amount_cents,
        },
    )
    return offer


async def decline(db: AsyncSession, offer_id: uuid.UUID, user: User) -> Offer:
    offer, conversation = await get_offer(db, offer_id, user)
    await _transition(db, offer, user, OfferStatus.DECLINED, by_proposer=False)
    await _append(db, conversation, user.id, MessageKind.OFFER_DECLINED, offer=offer)
    await _tell(
        db,
        conversation,
        offer.proposer_id,
        "offer_declined",
        {
            "name": user.display_name,
            "title": await _listing_title(db, conversation),
            "amount_cents": offer.amount_cents,
        },
    )
    return offer


async def counter(db: AsyncSession, offer_id: uuid.UUID, user: User, amount_cents: int) -> Offer:
    offer, conversation = await get_offer(db, offer_id, user)
    await _ensure_not_blocked(db, conversation)
    listing = await _offer_listing(db, conversation, offer.quantity)
    _check_amount(listing, amount_cents)
    await _transition(db, offer, user, OfferStatus.COUNTERED, by_proposer=False)
    new = await _insert_offer(db, conversation, user, amount_cents, offer.quantity)
    await _append(db, conversation, user.id, MessageKind.OFFER, offer=new)
    await _tell(
        db,
        conversation,
        offer.proposer_id,
        "offer_countered",
        {"name": user.display_name, "title": listing.title, "amount_cents": amount_cents},
    )
    return new


async def cancel(db: AsyncSession, offer_id: uuid.UUID, user: User) -> Offer:
    offer, conversation = await get_offer(db, offer_id, user)
    await _transition(db, offer, user, OfferStatus.CANCELLED, by_proposer=True)
    await _append(db, conversation, user.id, MessageKind.OFFER_CANCELLED, offer=offer)
    await wake(db, conversation.other(user.id))
    return offer


async def expire_due(db: AsyncSession) -> int:
    """Job: close offers nobody answered in time and tell whoever made them."""
    rows = (
        await db.execute(
            update(Offer)
            .where(Offer.status == OfferStatus.PENDING, Offer.expires_at <= func.now())
            .values(status=OfferStatus.EXPIRED, responded_at=func.now())
            .returning(Offer.id)
        )
    ).scalars()
    expired = 0
    for offer_id in list(rows):
        offer = await db.get(Offer, offer_id)
        conversation = await db.get(Conversation, offer.conversation_id) if offer else None
        if offer is None or conversation is None:
            continue
        await db.refresh(offer)
        await _append(db, conversation, None, MessageKind.OFFER_EXPIRED, offer=offer)
        await _tell(
            db,
            conversation,
            offer.proposer_id,
            "offer_expired",
            {"title": await _listing_title(db, conversation), "amount_cents": offer.amount_cents},
        )
        await wake(db, conversation.other(offer.proposer_id))
        expired += 1
    return expired


# --- paying an accepted offer ----------------------------------------------------------------


@dataclass(frozen=True)
class OfferTerms:
    offer_id: uuid.UUID
    listing_id: uuid.UUID
    quantity: int
    unit_price_cents: int


async def offer_checkouts(db: AsyncSession, offer_id: uuid.UUID) -> list[Checkout]:
    return list(
        (
            await db.execute(
                select(Checkout)
                .where(Checkout.offer_id == offer_id)
                .order_by(Checkout.created_at.desc())
            )
        ).scalars()
    )


def pay_by(offer: Offer) -> datetime | None:
    if offer.status != OfferStatus.ACCEPTED or offer.responded_at is None:
        return None
    return offer.responded_at + PAY_WINDOW


async def checkout_terms(
    db: AsyncSession, offer_id: uuid.UUID, user: User
) -> OfferTerms | Checkout:
    """What to charge for an accepted offer, or the checkout already open for it. Only the
    buyer of that conversation can pay it, at the agreed price and quantity only."""
    offer, conversation = await get_offer(db, offer_id, user)
    if conversation.buyer_id != user.id:
        raise Forbidden("Only the buyer pays an offer.", code="buyer_only")
    if offer.status != OfferStatus.ACCEPTED:
        raise Conflict("This offer was not accepted.", code="offer_not_accepted")
    for existing in await offer_checkouts(db, offer.id):
        if existing.status == CheckoutStatus.PAID:
            raise Conflict("This offer was already paid.", code="offer_used")
        if existing.status == CheckoutStatus.PENDING and existing.expires_at > _now():
            return existing
    deadline = pay_by(offer)
    if deadline is None or deadline <= _now():
        raise Conflict("The time to pay this offer has passed.", code="offer_expired")
    await _ensure_not_blocked(db, conversation)
    return OfferTerms(
        offer_id=offer.id,
        listing_id=conversation.listing_id,
        quantity=offer.quantity,
        unit_price_cents=offer.amount_cents,
    )
