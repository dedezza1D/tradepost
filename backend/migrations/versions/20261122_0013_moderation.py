"""Moderation: listings held for review, report triage and screened chat messages.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE listing_status ADD VALUE IF NOT EXISTS 'in_review'")
    op.add_column("listings", sa.Column("review_category", sa.String(20)))
    op.add_column("listings", sa.Column("review_reason", sa.String(300)))
    op.add_column("reports", sa.Column("triage_category", sa.String(20)))
    op.add_column("reports", sa.Column("triage_priority", sa.SmallInteger()))
    op.add_column("reports", sa.Column("triage_reason", sa.String(300)))
    op.add_column("reports", sa.Column("triaged_at", sa.DateTime(timezone=True)))
    op.add_column("messages", sa.Column("screened_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("messages", "screened_at")
    op.drop_column("reports", "triaged_at")
    op.drop_column("reports", "triage_reason")
    op.drop_column("reports", "triage_priority")
    op.drop_column("reports", "triage_category")
    op.drop_column("listings", "review_reason")
    op.drop_column("listings", "review_category")
    # PostgreSQL cannot drop an enum value; listings in review go back to drafts.
    op.execute("UPDATE listings SET status = 'draft' WHERE status = 'in_review'")
