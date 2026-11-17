"""Cart, checkout, orders, payments (attempts, ledger, webhook inbox, payouts) and reviews.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17 16:15:32.269692
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("provider_ref", sa.String(length=120), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_events")),
        sa.UniqueConstraint("provider", "event_id", name=op.f("uq_payment_events_provider")),
    )
    op.create_index(
        "ix_payment_events_pending", "payment_events", ["processed_at", "received_at"], unique=False
    )
    op.create_table(
        "checkouts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "paid", "expired", "cancelled", name="checkout_status"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["buyer_id"], ["users.id"], name=op.f("fk_checkouts_buyer_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checkouts")),
        sa.UniqueConstraint("buyer_id", "idempotency_key", name=op.f("uq_checkouts_buyer_id")),
    )
    op.create_index(
        "ix_checkouts_status_expires", "checkouts", ["status", "expires_at"], unique=False
    )
    op.create_table(
        "seller_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_account_id", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_seller_accounts_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seller_accounts")),
        sa.UniqueConstraint(
            "provider_account_id", name=op.f("uq_seller_accounts_provider_account_id")
        ),
        sa.UniqueConstraint("user_id", "provider", name=op.f("uq_seller_accounts_user_id")),
    )
    op.create_table(
        "cart_items",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_cart_items_quantity_positive")),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_cart_items_listing_id_listings"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_cart_items_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "listing_id", name=op.f("pk_cart_items")),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("checkout_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("seller_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "awaiting_payment",
                "paid",
                "shipped",
                "delivered",
                "cancelled",
                "refunded",
                name="order_status",
            ),
            nullable=False,
        ),
        sa.Column("items_cents", sa.BigInteger(), nullable=False),
        sa.Column("shipping_cents", sa.BigInteger(), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("platform_fee_cents", sa.BigInteger(), nullable=False),
        sa.Column("tracking_code", sa.String(length=60), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "total_cents = items_cents + shipping_cents", name=op.f("ck_orders_total")
        ),
        sa.CheckConstraint(
            "platform_fee_cents >= 0 AND platform_fee_cents <= total_cents",
            name=op.f("ck_orders_fee"),
        ),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], name=op.f("fk_orders_buyer_id_users")),
        sa.ForeignKeyConstraint(
            ["checkout_id"], ["checkouts.id"], name=op.f("fk_orders_checkout_id_checkouts")
        ),
        sa.ForeignKeyConstraint(
            ["seller_id"], ["users.id"], name=op.f("fk_orders_seller_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index("ix_orders_buyer", "orders", ["buyer_id", "created_at"], unique=False)
    op.create_index("ix_orders_seller", "orders", ["seller_id", "created_at"], unique=False)
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("checkout_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_ref", sa.String(length=120), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "succeeded", "failed", "cancelled", "refunded", name="attempt_status"
            ),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("charge_ref", sa.String(length=120), nullable=True),
        sa.Column("status_detail", sa.String(length=80), nullable=True),
        sa.Column("client_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["checkout_id"],
            ["checkouts.id"],
            name=op.f("fk_payment_attempts_checkout_id_checkouts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_attempts")),
        sa.UniqueConstraint("provider", "provider_ref", name=op.f("uq_payment_attempts_provider")),
    )
    op.create_index(
        op.f("ix_payment_attempts_checkout_id"), "payment_attempts", ["checkout_id"], unique=False
    )
    op.create_index(
        "ix_payment_attempts_status", "payment_attempts", ["status", "created_at"], unique=False
    )
    op.create_table(
        "stock_reservations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("checkout_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["checkout_id"],
            ["checkouts.id"],
            name=op.f("fk_stock_reservations_checkout_id_checkouts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"], ["listings.id"], name=op.f("fk_stock_reservations_listing_id_listings")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_reservations")),
    )
    op.create_index(
        op.f("ix_stock_reservations_checkout_id"),
        "stock_reservations",
        ["checkout_id"],
        unique=False,
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("seller_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "sale",
                "platform_fee",
                "payout",
                "refund",
                "fee_reversal",
                "payout_reversal",
                name="ledger_kind",
            ),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("provider_ref", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_ledger_entries_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["seller_id"], ["users.id"], name=op.f("fk_ledger_entries_seller_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("order_id", "kind", name=op.f("uq_ledger_entries_order_id")),
    )
    op.create_index("ix_ledger_seller", "ledger_entries", ["seller_id", "created_at"], unique=False)
    op.create_table(
        "order_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_events_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_events")),
    )
    op.create_index(op.f("ix_order_events_order_id"), "order_events", ["order_id"], unique=False)
    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("shipping_cents", sa.BigInteger(), nullable=False),
        sa.Column("thumb_key", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["listing_id"], ["listings.id"], name=op.f("fk_order_items_listing_id_listings")
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_order_items_order_id_orders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)
    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("buyer_id", sa.Uuid(), nullable=False),
        sa.Column("seller_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name=op.f("ck_reviews_rating_range")),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], name=op.f("fk_reviews_buyer_id_users")),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_reviews_order_id_orders")
        ),
        sa.ForeignKeyConstraint(
            ["seller_id"], ["users.id"], name=op.f("fk_reviews_seller_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint("order_id", name=op.f("uq_reviews_order_id")),
    )
    op.create_index(op.f("ix_reviews_seller_id"), "reviews", ["seller_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reviews_seller_id"), table_name="reviews")
    op.drop_table("reviews")
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")
    op.drop_index(op.f("ix_order_events_order_id"), table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_ledger_seller", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index(op.f("ix_stock_reservations_checkout_id"), table_name="stock_reservations")
    op.drop_table("stock_reservations")
    op.drop_index("ix_payment_attempts_status", table_name="payment_attempts")
    op.drop_index(op.f("ix_payment_attempts_checkout_id"), table_name="payment_attempts")
    op.drop_table("payment_attempts")
    op.drop_index("ix_orders_seller", table_name="orders")
    op.drop_index("ix_orders_buyer", table_name="orders")
    op.drop_table("orders")
    op.drop_table("cart_items")
    op.drop_table("seller_accounts")
    op.drop_index("ix_checkouts_status_expires", table_name="checkouts")
    op.drop_table("checkouts")
    op.drop_index("ix_payment_events_pending", table_name="payment_events")
    op.drop_table("payment_events")
    for enum_name in ("ledger_kind", "attempt_status", "order_status", "checkout_status"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
