"""Cart, checkout, orders, stock reservations, payment attempts, ledger and webhook inbox.

One checkout is one payment by one buyer. It can contain items from several sellers, so it is
split into one order per seller: each seller ships, gets paid and is reviewed on their own.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tradepost.db import Base, TimestampMixin, new_id


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class CheckoutStatus(enum.StrEnum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class OrderStatus(enum.StrEnum):
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class AttemptStatus(enum.StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class LedgerKind(enum.StrEnum):
    SALE = "sale"  # + order total credited to the seller
    PLATFORM_FEE = "platform_fee"  # - commission kept by the platform
    PAYOUT = "payout"  # - money transferred to the seller's provider account
    REFUND = "refund"  # - order total returned to the buyer
    FEE_REVERSAL = "fee_reversal"  # + commission given back on refund
    PAYOUT_REVERSAL = "payout_reversal"  # + transfer pulled back on refund


class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Checkout(TimestampMixin, Base):
    __tablename__ = "checkouts"
    __table_args__ = (
        UniqueConstraint("buyer_id", "idempotency_key"),
        Index("ix_checkouts_status_expires", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[CheckoutStatus] = mapped_column(
        _enum(CheckoutStatus, "checkout_status"), nullable=False, default=CheckoutStatus.PENDING
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    idempotency_key: Mapped[str] = mapped_column(String(80), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The attempt that paid. Any other attempt that succeeds later is a double payment.
    paid_attempt_id: Mapped[uuid.UUID | None] = mapped_column()
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the checkout pays an accepted offer: its items are charged at the agreed price.
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("offers.id", ondelete="SET NULL"), index=True
    )

    orders: Mapped[list["Order"]] = relationship(
        lazy="selectin", order_by="Order.created_at", back_populates="checkout"
    )
    attempts: Mapped[list["PaymentAttempt"]] = relationship(
        lazy="selectin", order_by="PaymentAttempt.created_at"
    )


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_buyer", "buyer_id", "created_at"),
        Index("ix_orders_seller", "seller_id", "created_at"),
        CheckConstraint("platform_fee_cents >= 0 AND platform_fee_cents <= total_cents", "fee"),
        CheckConstraint("total_cents = items_cents + shipping_cents", "total"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    checkout_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("checkouts.id"), nullable=False)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "order_status"), nullable=False, default=OrderStatus.AWAITING_PAYMENT
    )
    items_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    shipping_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    platform_fee_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tracking_code: Mapped[str | None] = mapped_column(String(60))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    checkout: Mapped[Checkout] = relationship(back_populates="orders", lazy="joined")
    items: Mapped[list["OrderItem"]] = relationship(lazy="selectin", order_by="OrderItem.id")

    @property
    def seller_net_cents(self) -> int:
        return self.total_cents - self.platform_fee_cents


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id"), nullable=False)
    # Snapshot: what the buyer saw when paying, whatever the seller edits later.
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thumb_key: Mapped[str | None] = mapped_column(String(200))


class OrderEvent(Base):
    """Audit trail of every status change."""

    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StockReservation(Base):
    __tablename__ = "stock_reservations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    checkout_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checkouts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentAttempt(TimestampMixin, Base):
    """One try at paying a checkout with a provider (a Stripe PaymentIntent, a Mercado Pago
    order). A buyer may try Pix, abandon it and pay by card: every attempt is tracked, so a late
    payment of an abandoned attempt is still noticed (and refunded)."""

    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_ref"),
        Index("ix_payment_attempts_status", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    checkout_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("checkouts.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[AttemptStatus] = mapped_column(
        _enum(AttemptStatus, "attempt_status"), nullable=False, default=AttemptStatus.PENDING
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Card network / bank reference needed later (Stripe charge id for transfers).
    charge_ref: Mapped[str | None] = mapped_column(String(120))
    status_detail: Mapped[str | None] = mapped_column(String(80))
    # What the web app needs to show (Stripe client secret, Pix QR code). Never card data.
    client_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class LedgerEntry(Base):
    """Append-only money movements per order and seller. Balances are sums, never stored."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("order_id", "kind"),
        Index("ix_ledger_seller", "seller_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[LedgerKind] = mapped_column(_enum(LedgerKind, "ledger_kind"), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PaymentEvent(Base):
    """Webhook inbox. The unique (provider, event_id) makes redelivered events no-ops."""

    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id"),
        Index("ix_payment_events_pending", "processed_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String(120))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class SellerAccount(TimestampMixin, Base):
    """A seller's account at a payment provider, used for payouts (Stripe Connect)."""

    __tablename__ = "seller_accounts"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
