"""Reference exchange rates (approximate prices in other currencies).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("quoted_on", sa.Date(), primary_key=True),
        sa.Column("brl_per_unit", sa.Float(), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("fx_rates")
