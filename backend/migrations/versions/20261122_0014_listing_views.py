"""Daily views per listing, for the seller dashboard.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "listing_daily_views",
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey(
                "listings.id", ondelete="CASCADE", name="fk_listing_daily_views_listing_id_listings"
            ),
            primary_key=True,
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("views", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("listing_daily_views")
