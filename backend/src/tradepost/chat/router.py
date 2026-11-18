import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select

from tradepost.catalog.models import Listing, ListingPhoto, ListingStatus
from tradepost.chat import service
from tradepost.chat.models import Conversation, Message, MessageKind, Offer, OfferStatus
from tradepost.deps import DbSession
from tradepost.identity.models import User
from tradepost.identity.sessions import CurrentUser
from tradepost.media.storage import Storage
from tradepost.orders.models import CheckoutStatus
from tradepost.security.ratelimit import rate_limit
from tradepost.social.blocks import is_blocked

router = APIRouter(prefix="/api", tags=["chat"])

Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Amount = Annotated[int, Field(gt=0, le=100_000_000)]


def _storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


StorageDep = Annotated[Storage, Depends(_storage)]


class StartIn(BaseModel):
    listing_id: uuid.UUID
    body: Body | None = None


class MessageIn(BaseModel):
    body: Body


class OfferIn(BaseModel):
    amount_cents: Amount
    quantity: int = Field(default=1, ge=1, le=10)


class CounterIn(BaseModel):
    amount_cents: Amount


class ListingSummary(BaseModel):
    id: uuid.UUID
    title: str
    price_cents: int
    status: ListingStatus
    thumb_url: str | None


class PersonOut(BaseModel):
    id: uuid.UUID
    display_name: str
    avatar_url: str | None


class ConversationOut(BaseModel):
    id: uuid.UUID
    role: Literal["buyer", "seller"]
    listing: ListingSummary
    other: PersonOut
    last_message_at: datetime
    last_message_preview: str | None
    unread: int
    # When the other person last read the conversation ("seen" marks).
    other_read_at: datetime | None
    blocked: bool


class InboxOut(BaseModel):
    items: list[ConversationOut]
    next_before: datetime | None
    unread_total: int


class OfferOut(BaseModel):
    id: uuid.UUID
    proposer_id: uuid.UUID
    amount_cents: int
    quantity: int
    status: OfferStatus
    expires_at: datetime
    pay_by: datetime | None
    # The open checkout paying this offer, if any, and whether it was paid.
    checkout_id: uuid.UUID | None
    paid: bool


class MessageOut(BaseModel):
    id: int
    sender_id: uuid.UUID | None
    kind: MessageKind
    body: str | None
    warning: str | None
    created_at: datetime
    offer: OfferOut | None


class MessagePageOut(BaseModel):
    items: list[MessageOut]
    has_older: bool


class UnreadOut(BaseModel):
    count: int


async def _conversation_out(
    db: DbSession,
    storage: Storage,
    conversation: Conversation,
    user: User,
    unread: int | None = None,
) -> ConversationOut:
    listing = await db.get(Listing, conversation.listing_id)
    other = await db.get(User, conversation.other(user.id))
    assert listing is not None
    assert other is not None
    thumb = (
        await db.execute(
            select(ListingPhoto.key_thumb)
            .where(ListingPhoto.listing_id == listing.id)
            .order_by(ListingPhoto.position)
            .limit(1)
        )
    ).scalar_one_or_none()
    role = conversation.role_of(user.id)
    assert role in ("buyer", "seller")
    if unread is None:
        unread = (await service.unread_counts(db, user, [conversation.id])).get(conversation.id, 0)
    return ConversationOut(
        id=conversation.id,
        role=role,
        listing=ListingSummary(
            id=listing.id,
            title=listing.title,
            price_cents=listing.price_cents,
            status=listing.status,
            thumb_url=storage.url(thumb) if thumb else None,
        ),
        other=PersonOut(
            id=other.id,
            display_name=other.display_name,
            avatar_url=storage.url(other.avatar_key) if other.avatar_key else None,
        ),
        last_message_at=conversation.last_message_at,
        last_message_preview=conversation.last_message_preview,
        unread=unread,
        other_read_at=(
            conversation.seller_read_at if role == "buyer" else conversation.buyer_read_at
        ),
        blocked=await is_blocked(db, conversation.buyer_id, conversation.seller_id),
    )


async def _offer_out(db: DbSession, offer: Offer) -> OfferOut:
    checkouts = await service.offer_checkouts(db, offer.id)
    paid = any(c.status == CheckoutStatus.PAID for c in checkouts)
    open_checkout = next((c for c in checkouts if c.status == CheckoutStatus.PENDING), None)
    return OfferOut(
        id=offer.id,
        proposer_id=offer.proposer_id,
        amount_cents=offer.amount_cents,
        quantity=offer.quantity,
        status=offer.status,
        expires_at=offer.expires_at,
        pay_by=service.pay_by(offer),
        checkout_id=open_checkout.id if open_checkout else None,
        paid=paid,
    )


async def _messages_out(db: DbSession, rows: list[Message]) -> list[MessageOut]:
    offers: dict[uuid.UUID, OfferOut] = {}
    out = []
    for m in rows:
        offer_out = None
        if m.offer_id is not None:
            if m.offer_id not in offers:
                offer = await db.get(Offer, m.offer_id)
                if offer is not None:
                    await db.refresh(offer)
                    offers[m.offer_id] = await _offer_out(db, offer)
            offer_out = offers.get(m.offer_id)
        out.append(
            MessageOut(
                id=m.id,
                sender_id=m.sender_id,
                kind=m.kind,
                body=m.body,
                warning=m.warning,
                created_at=m.created_at,
                offer=offer_out,
            )
        )
    return out


# --- conversations ---------------------------------------------------------------------------


@router.post(
    "/conversations",
    dependencies=[Depends(rate_limit("conversation_start", limit=60, window_seconds=3600))],
)
async def start_conversation(
    body: StartIn, user: CurrentUser, db: DbSession, storage: StorageDep
) -> ConversationOut:
    """Open (or reopen) the conversation about a listing, optionally with a first message."""
    conversation = await service.start(db, user, body.listing_id)
    if body.body:
        await service.send(db, conversation, user, body.body)
    return await _conversation_out(db, storage, conversation, user)


@router.get("/conversations")
async def list_conversations(
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> InboxOut:
    rows = await service.inbox(db, user, before, limit)
    unread = await service.unread_counts(db, user, [c.id for c in rows])
    return InboxOut(
        items=[await _conversation_out(db, storage, c, user, unread.get(c.id, 0)) for c in rows],
        next_before=rows[-1].last_message_at if len(rows) == limit else None,
        unread_total=await service.unread_total(db, user),
    )


@router.get("/conversations/unread")
async def unread(user: CurrentUser, db: DbSession) -> UnreadOut:
    return UnreadOut(count=await service.unread_total(db, user))


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> ConversationOut:
    conversation = await service.get(db, conversation_id, user)
    return await _conversation_out(db, storage, conversation, user)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    before: Annotated[int | None, Query(ge=1)] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=service.MAX_PAGE)] = service.MAX_PAGE,
) -> MessagePageOut:
    conversation = await service.get(db, conversation_id, user)
    rows = await service.messages(db, conversation, before=before, after=after, limit=limit)
    has_older = after is None and len(rows) == limit
    return MessagePageOut(items=await _messages_out(db, rows), has_older=has_older)


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("message", limit=40, window_seconds=60))],
)
async def send_message(
    conversation_id: uuid.UUID, body: MessageIn, user: CurrentUser, db: DbSession
) -> MessageOut:
    conversation = await service.get(db, conversation_id, user)
    message = await service.send(db, conversation, user, body.body)
    return (await _messages_out(db, [message]))[0]


@router.post("/conversations/{conversation_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(conversation_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    conversation = await service.get(db, conversation_id, user)
    await service.mark_read(db, conversation, user)


# --- offers ----------------------------------------------------------------------------------

_offer_limit = Depends(rate_limit("offer", limit=30, window_seconds=3600))


@router.post(
    "/conversations/{conversation_id}/offers",
    status_code=status.HTTP_201_CREATED,
    dependencies=[_offer_limit],
)
async def make_offer(
    conversation_id: uuid.UUID, body: OfferIn, user: CurrentUser, db: DbSession
) -> OfferOut:
    conversation = await service.get(db, conversation_id, user)
    offer = await service.make_offer(db, conversation, user, body.amount_cents, body.quantity)
    return await _offer_out(db, offer)


@router.post("/offers/{offer_id}/accept", dependencies=[_offer_limit])
async def accept_offer(offer_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OfferOut:
    return await _offer_out(db, await service.accept(db, offer_id, user))


@router.post("/offers/{offer_id}/decline", dependencies=[_offer_limit])
async def decline_offer(offer_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OfferOut:
    return await _offer_out(db, await service.decline(db, offer_id, user))


@router.post("/offers/{offer_id}/counter", dependencies=[_offer_limit])
async def counter_offer(
    offer_id: uuid.UUID, body: CounterIn, user: CurrentUser, db: DbSession
) -> OfferOut:
    return await _offer_out(db, await service.counter(db, offer_id, user, body.amount_cents))


@router.post("/offers/{offer_id}/cancel", dependencies=[_offer_limit])
async def cancel_offer(offer_id: uuid.UUID, user: CurrentUser, db: DbSession) -> OfferOut:
    return await _offer_out(db, await service.cancel(db, offer_id, user))
