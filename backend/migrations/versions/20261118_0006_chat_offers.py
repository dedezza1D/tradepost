"""Chat: conversations per listing and buyer, messages, offers; checkouts that pay an offer.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OFFER_STATUS = ("pending", "accepted", "declined", "countered", "cancelled", "expired")
MESSAGE_KIND = (
    "text",
    "offer",
    "offer_accepted",
    "offer_declined",
    "offer_cancelled",
    "offer_expired",
)


def _user(name: str, *, nullable: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=nullable
    )


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("listing_id", sa.Uuid(), sa.ForeignKey("listings.id"), nullable=False),
        _user("buyer_id"),
        _user("seller_id"),
        _now("created_at"),
        _now("last_message_at"),
        sa.Column("last_message_preview", sa.String(140)),
        sa.Column("buyer_read_at", sa.DateTime(timezone=True)),
        sa.Column("seller_read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("listing_id", "buyer_id"),
        sa.CheckConstraint("buyer_id <> seller_id", name="two_people"),
    )
    op.create_index("ix_conversations_buyer", "conversations", ["buyer_id", "last_message_at"])
    op.create_index("ix_conversations_seller", "conversations", ["seller_id", "last_message_at"])

    offer_status = postgresql.ENUM(*OFFER_STATUS, name="offer_status", create_type=False)
    message_kind = postgresql.ENUM(*MESSAGE_KIND, name="message_kind", create_type=False)
    offer_status.create(op.get_bind(), checkfirst=False)
    message_kind.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "offers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user("proposer_id"),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.SmallInteger(), nullable=False),
        sa.Column("status", offer_status, nullable=False),
        _now("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("amount_cents > 0", name="amount_positive"),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
    )
    op.create_index("ix_offers_conversation_id", "offers", ["conversation_id"])
    op.create_index("ix_offers_status_expires", "offers", ["status", "expires_at"])
    op.create_index(
        "uq_offers_one_pending",
        "offers",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user("sender_id", nullable=True),
        sa.Column("kind", message_kind, nullable=False),
        sa.Column("body", sa.String(1000)),
        sa.Column("offer_id", sa.Uuid(), sa.ForeignKey("offers.id", ondelete="CASCADE")),
        sa.Column("warning", sa.String(30)),
        _now("created_at"),
    )
    op.create_index("ix_messages_conversation", "messages", ["conversation_id", "id"])

    op.add_column(
        "checkouts",
        sa.Column("offer_id", sa.Uuid(), sa.ForeignKey("offers.id", ondelete="SET NULL")),
    )
    op.create_index("ix_checkouts_offer_id", "checkouts", ["offer_id"])


def downgrade() -> None:
    op.drop_index("ix_checkouts_offer_id", table_name="checkouts")
    op.drop_column("checkouts", "offer_id")
    op.drop_table("messages")
    op.drop_table("offers")
    op.drop_table("conversations")
    op.execute("DROP TYPE message_kind")
    op.execute("DROP TYPE offer_status")
