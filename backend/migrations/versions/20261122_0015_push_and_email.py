"""Notifications outside the app: push subscriptions, preferences and the delivery outbox.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid()),
        sa.Column("endpoint", sa.String(1000), nullable=False),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth", sa.String(50), nullable=False),
        sa.Column("user_agent", sa.String(200)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_push_subscriptions_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            ondelete="SET NULL",
            name="fk_push_subscriptions_session_id_sessions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_push_subscriptions"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])

    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("push", sa.Boolean(), nullable=False),
        sa.Column("email", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_notification_preferences_user_id_users",
        ),
        sa.PrimaryKeyConstraint("user_id", "category", name="pk_notification_preferences"),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), server_default="pending", nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(200)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            ondelete="CASCADE",
            name="fk_notification_deliveries_notification_id_notifications",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_deliveries"),
        sa.UniqueConstraint(
            "notification_id", "channel", name="uq_notification_deliveries_notification_id"
        ),
    )
    op.create_index(
        "ix_notification_deliveries_due",
        "notification_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("notification_preferences")
    op.drop_table("push_subscriptions")
