"""Social layer: follows, saves, blocks, reports, profile bio and avatar, counters.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPORT_TARGET = ("listing", "user")
REPORT_REASON = ("prohibited", "scam", "offensive", "spam", "other")
REPORT_STATUS = ("open", "dismissed", "actioned")


def _user_fk(name: str, *, primary_key: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=primary_key,
        nullable=False,
    )


def _created_at() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.add_column("users", sa.Column("bio", sa.String(300)))
    op.add_column("users", sa.Column("avatar_key", sa.String(200)))
    for column in ("avatar_bytes", "follower_count", "following_count"):
        op.add_column("users", sa.Column(column, sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "listings", sa.Column("save_count", sa.Integer(), nullable=False, server_default="0")
    )

    op.create_table(
        "follows",
        _user_fk("follower_id", primary_key=True),
        _user_fk("followee_id", primary_key=True),
        _created_at(),
        sa.CheckConstraint("follower_id <> followee_id", name="not_self"),
    )
    op.create_index("ix_follows_followee", "follows", ["followee_id", "created_at"])
    op.create_index("ix_follows_follower_created", "follows", ["follower_id", "created_at"])

    op.create_table(
        "saves",
        _user_fk("user_id", primary_key=True),
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey("listings.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        _created_at(),
        sa.Column("price_cents_at_save", sa.BigInteger(), nullable=False),
        sa.Column("alerted_price_cents", sa.BigInteger()),
        sa.Column("low_stock_alerted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_saves_listing", "saves", ["listing_id"])
    op.create_index("ix_saves_user_created", "saves", ["user_id", "created_at"])
    op.create_index("ix_saves_created", "saves", ["created_at"])

    op.create_table(
        "blocks",
        _user_fk("blocker_id", primary_key=True),
        _user_fk("blocked_id", primary_key=True),
        _created_at(),
        sa.CheckConstraint("blocker_id <> blocked_id", name="not_self"),
    )
    op.create_index("ix_blocks_blocked", "blocks", ["blocked_id"])

    target = postgresql.ENUM(*REPORT_TARGET, name="report_target", create_type=False)
    reason = postgresql.ENUM(*REPORT_REASON, name="report_reason", create_type=False)
    status = postgresql.ENUM(*REPORT_STATUS, name="report_status", create_type=False)
    for enum in (target, reason, status):
        enum.create(op.get_bind(), checkfirst=False)
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _user_fk("reporter_id"),
        sa.Column("target_type", target, nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("reason", reason, nullable=False),
        sa.Column("details", sa.String(1000)),
        sa.Column("status", status, nullable=False),
        _created_at(),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index(
        "uq_reports_open_per_reporter",
        "reports",
        ["reporter_id", "target_type", "target_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index("ix_reports_status_created", "reports", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("reports")
    for name in ("report_status", "report_reason", "report_target"):
        op.execute(f"DROP TYPE {name}")
    op.drop_table("blocks")
    op.drop_table("saves")
    op.drop_table("follows")
    op.drop_column("listings", "save_count")
    for column in ("following_count", "follower_count", "avatar_bytes", "avatar_key", "bio"):
        op.drop_column("users", column)
