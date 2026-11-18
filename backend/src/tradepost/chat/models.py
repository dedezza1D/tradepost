"""Conversations between a buyer and a seller about one listing, their messages and offers."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base, new_id


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("listing_id", "buyer_id"),
        CheckConstraint("buyer_id <> seller_id", name="two_people"),
        Index("ix_conversations_buyer", "buyer_id", "last_message_at"),
        Index("ix_conversations_seller", "seller_id", "last_message_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id"), nullable=False)
    buyer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Text of the last message, or "<kind>:<amount>" for offer events (rendered by the web app
    # in the reader's language).
    last_message_preview: Mapped[str | None] = mapped_column(String(140))
    # Everything up to these instants was read; "seen" marks come from the other side's value.
    buyer_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seller_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def role_of(self, user_id: uuid.UUID) -> str | None:
        if user_id == self.buyer_id:
            return "buyer"
        if user_id == self.seller_id:
            return "seller"
        return None

    def other(self, user_id: uuid.UUID) -> uuid.UUID:
        return self.seller_id if user_id == self.buyer_id else self.buyer_id


class OfferStatus(enum.StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    COUNTERED = "countered"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Offer(Base):
    """A proposed unit price. The other participant accepts, declines or counters it; a counter
    closes this offer and opens a new one from the other side."""

    __tablename__ = "offers"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        # One open negotiation step at a time per conversation.
        Index(
            "uq_offers_one_pending",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_offers_status_expires", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    proposer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    status: Mapped[OfferStatus] = mapped_column(
        _enum(OfferStatus, "offer_status"), nullable=False, default=OfferStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageKind(enum.StrEnum):
    TEXT = "text"
    OFFER = "offer"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_DECLINED = "offer_declined"
    OFFER_CANCELLED = "offer_cancelled"
    OFFER_EXPIRED = "offer_expired"


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # Empty for events the system writes (an offer that expired).
    sender_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[MessageKind] = mapped_column(_enum(MessageKind, "message_kind"), nullable=False)
    body: Mapped[str | None] = mapped_column(String(1000))
    offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"))
    # Set when the text looks like contact details or payment outside the platform.
    warning: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
