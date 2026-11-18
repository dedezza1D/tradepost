"""Seller coupons, card installments, disputes.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    kind = postgresql.ENUM("percent", "fixed", name="coupon_kind", create_type=False)
    kind.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "coupons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "seller_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("kind", kind, nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("min_items_cents", sa.BigInteger(), nullable=False),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("active", sa.Boolean(), nullable=False),
        _now("created_at"),
        sa.UniqueConstraint("seller_id", "code"),
        sa.CheckConstraint("value > 0", name="value_positive"),
        sa.CheckConstraint("max_uses IS NULL OR uses <= max_uses", name="uses_within_limit"),
    )
    op.create_index("ix_coupons_seller_id", "coupons", ["seller_id"])

    op.add_column(
        "orders", sa.Column("discount_cents", sa.BigInteger(), nullable=False, server_default="0")
    )
    op.add_column("orders", sa.Column("coupon_id", sa.Uuid(), sa.ForeignKey("coupons.id")))
    op.create_index("ix_orders_coupon_id", "orders", ["coupon_id"])
    op.drop_constraint("total", "orders", type_="check")
    op.create_check_constraint(
        "total", "orders", "total_cents = items_cents + shipping_cents - discount_cents"
    )
    op.add_column("payment_attempts", sa.Column("installments", sa.SmallInteger()))

    reason = postgresql.ENUM(
        "not_received",
        "not_as_described",
        "damaged",
        "other",
        name="dispute_reason",
        create_type=False,
    )
    status = postgresql.ENUM(
        "open",
        "seller_rejected",
        "escalated",
        "refunded",
        "rejected",
        "cancelled",
        name="dispute_status",
        create_type=False,
    )
    reason.create(op.get_bind(), checkfirst=False)
    status.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "disputes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("order_id", sa.Uuid(), sa.ForeignKey("orders.id"), nullable=False, unique=True),
        sa.Column("buyer_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("seller_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reason", reason, nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("refund_cents", sa.BigInteger()),
        sa.Column("resolution_note", sa.String(500)),
        sa.Column("resolved_by", sa.String(20)),
        _now("created_at"),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_disputes_status", "disputes", ["status", "created_at"])
    op.create_table(
        "dispute_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "dispute_id",
            sa.Uuid(),
            sa.ForeignKey("disputes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_role", sa.String(10), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        _now("created_at"),
    )
    op.create_index("ix_dispute_messages_dispute_id", "dispute_messages", ["dispute_id"])


def downgrade() -> None:
    op.drop_table("dispute_messages")
    op.drop_table("disputes")
    op.execute("DROP TYPE dispute_status")
    op.execute("DROP TYPE dispute_reason")
    op.drop_column("payment_attempts", "installments")
    op.drop_constraint("total", "orders", type_="check")
    op.create_check_constraint("total", "orders", "total_cents = items_cents + shipping_cents")
    op.drop_index("ix_orders_coupon_id", table_name="orders")
    op.drop_column("orders", "coupon_id")
    op.drop_column("orders", "discount_cents")
    op.drop_table("coupons")
    op.execute("DROP TYPE coupon_kind")
