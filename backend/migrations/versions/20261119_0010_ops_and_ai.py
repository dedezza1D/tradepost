"""Operations (request metrics, errors, job runs, alerts, switches) and the AI call log.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "request_stats",
        sa.Column("minute", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("method", sa.String(8), primary_key=True),
        sa.Column("route", sa.String(160), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("errors_5xx", sa.Integer(), nullable=False),
        sa.Column("errors_4xx", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.Float(), nullable=False),
        sa.Column("buckets", postgresql.ARRAY(sa.Integer()), nullable=False),
    )
    op.create_table(
        "error_events",
        sa.Column("fingerprint", sa.String(40), primary_key=True),
        sa.Column("kind", sa.String(120), nullable=False),
        sa.Column("route", sa.String(160), nullable=False),
        sa.Column("message", sa.String(300), nullable=False),
        sa.Column("traceback", sa.Text(), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        _now("first_seen"),
        _now("last_seen"),
        sa.Column("last_request_id", sa.String(64)),
    )
    op.create_table(
        "job_runs",
        sa.Column("name", sa.String(60), primary_key=True),
        sa.Column("last_started", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_ok_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(300)),
        sa.Column("consecutive_failures", sa.SmallInteger(), nullable=False),
        sa.Column("last_count", sa.Integer(), nullable=False),
        sa.Column("last_duration_ms", sa.Float(), nullable=False),
    )
    op.create_table(
        "alert_state",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "feature_flags",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        _now("updated_at"),
    )
    op.create_table(
        "ai_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        _now("created_at"),
        sa.Column("feature", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(300)),
    )
    op.create_index("ix_ai_calls_created", "ai_calls", ["created_at"])
    op.create_table(
        "ai_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("feature", sa.String(40), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=False),
        _now("created_at"),
    )


def downgrade() -> None:
    for table in (
        "ai_cache",
        "ai_calls",
        "feature_flags",
        "alert_state",
        "job_runs",
        "error_events",
        "request_stats",
    ):
        op.drop_table(table)
