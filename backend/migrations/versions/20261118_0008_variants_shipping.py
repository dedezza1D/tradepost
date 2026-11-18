"""Listing variants with their own stock, and calculated shipping.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-18

Expand only: existing listings keep fixed shipping and no variants; cart lines get the nil
variant, which stands for "no variant".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NIL = "'00000000-0000-0000-0000-000000000000'"


def _variant_fk(table: str) -> None:
    op.add_column(table, sa.Column("variant_id", sa.Uuid(), sa.ForeignKey("listing_variants.id")))


def upgrade() -> None:
    mode = postgresql.ENUM("fixed", "calculated", name="shipping_mode", create_type=False)
    mode.create(op.get_bind(), checkfirst=False)
    op.add_column(
        "listings", sa.Column("shipping_mode", mode, nullable=False, server_default="fixed")
    )
    op.add_column("listings", sa.Column("weight_grams", sa.Integer()))
    for side in ("length_cm", "width_cm", "height_cm"):
        op.add_column("listings", sa.Column(side, sa.SmallInteger()))

    op.create_table(
        "listing_variants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.CheckConstraint("stock >= 0", name="variant_stock_non_negative"),
    )
    op.create_index("ix_listing_variants_listing_id", "listing_variants", ["listing_id"])
    op.create_index(
        "uq_listing_variants_name",
        "listing_variants",
        ["listing_id", "name"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.add_column(
        "cart_items",
        sa.Column("variant_id", sa.Uuid(), nullable=False, server_default=sa.text(NIL)),
    )
    op.drop_constraint("pk_cart_items", "cart_items", type_="primary")
    op.create_primary_key("pk_cart_items", "cart_items", ["user_id", "listing_id", "variant_id"])

    _variant_fk("stock_reservations")
    _variant_fk("order_items")
    op.add_column("order_items", sa.Column("variant_name", sa.String(40)))
    _variant_fk("offers")

    op.add_column("orders", sa.Column("shipping_service", sa.String(20)))
    op.add_column("orders", sa.Column("shipping_days", sa.SmallInteger()))
    op.add_column("orders", sa.Column("ship_to", sa.String(90)))


def downgrade() -> None:
    for column in ("ship_to", "shipping_days", "shipping_service"):
        op.drop_column("orders", column)
    op.drop_column("offers", "variant_id")
    op.drop_column("order_items", "variant_name")
    op.drop_column("order_items", "variant_id")
    op.drop_column("stock_reservations", "variant_id")
    op.drop_constraint("pk_cart_items", "cart_items", type_="primary")
    op.execute("DELETE FROM cart_items WHERE variant_id <> '00000000-0000-0000-0000-000000000000'")
    op.create_primary_key("pk_cart_items", "cart_items", ["user_id", "listing_id"])
    op.drop_column("cart_items", "variant_id")
    op.drop_table("listing_variants")
    for column in ("height_cm", "width_cm", "length_cm", "weight_grams", "shipping_mode"):
        op.drop_column("listings", column)
    op.execute("DROP TYPE shipping_mode")
