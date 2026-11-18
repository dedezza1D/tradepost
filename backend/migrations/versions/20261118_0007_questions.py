"""Public questions on listings.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asker_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("answer", sa.String(1000)),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("hidden_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_questions_listing", "questions", ["listing_id", "created_at"])


def downgrade() -> None:
    op.drop_table("questions")
