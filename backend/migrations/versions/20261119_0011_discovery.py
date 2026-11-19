"""Discovery: listing embeddings for semantic search (pgvector) and saved searches.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # The vector column has no fixed dimension, so one table holds embeddings of several models
    # (the primary key includes the model). Written with SQL: Alembic has no pgvector type.
    op.execute(
        """
        CREATE TABLE listing_embeddings (
            listing_id uuid NOT NULL
                CONSTRAINT fk_listing_embeddings_listing_id_listings
                REFERENCES listings (id) ON DELETE CASCADE,
            model varchar(80) NOT NULL,
            content_hash varchar(64) NOT NULL,
            embedding vector NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_listing_embeddings PRIMARY KEY (listing_id, model)
        )
        """
    )

    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_saved_searches_user_id_users"),
            nullable=False,
        ),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("user_id", "signature", name="uq_saved_searches_user_id"),
    )
    op.create_index("ix_saved_searches_user_id", "saved_searches", ["user_id"])
    op.create_index("ix_saved_searches_checked_at", "saved_searches", ["checked_at"])


def downgrade() -> None:
    op.drop_table("saved_searches")
    op.execute("DROP TABLE listing_embeddings")
