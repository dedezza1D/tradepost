"""Municipalities (IBGE) with distance search, user locale and city, notifications.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18

Municipality data: kelvins/municipios-brasileiros (MIT), see migrations/data.
"""

import csv
import unicodedata
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA = Path(__file__).resolve().parent.parent / "data"


def _key(name: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return " ".join(plain.lower().replace("'", " ").replace("-", " ").split())


def _rows() -> list[dict[str, object]]:
    with (DATA / "estados.csv").open(encoding="utf-8-sig", newline="") as f:
        uf_by_code = {row["codigo_uf"]: row["uf"] for row in csv.DictReader(f)}
    with (DATA / "municipios.csv").open(encoding="utf-8-sig", newline="") as f:
        return [
            {
                "ibge_code": int(row["codigo_ibge"]),
                "name": row["nome"],
                "name_key": _key(row["nome"]),
                "state": uf_by_code[row["codigo_uf"]],
                "lat": float(row["latitude"]),
                "lng": float(row["longitude"]),
            }
            for row in csv.DictReader(f)
        ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS cube")
    op.execute("CREATE EXTENSION IF NOT EXISTS earthdistance")

    municipalities = op.create_table(
        "municipalities",
        sa.Column("ibge_code", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("name_key", sa.String(80), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
    )
    op.bulk_insert(municipalities, _rows())
    op.create_index("ix_municipalities_name_key", "municipalities", ["name_key", "state"])
    op.execute(
        "CREATE INDEX ix_municipalities_earth ON municipalities USING gist (ll_to_earth(lat, lng))"
    )

    # Expand: nullable columns, filled below; the running release ignores them.
    op.add_column(
        "users", sa.Column("locale", sa.String(5), nullable=False, server_default="pt-BR")
    )
    op.add_column(
        "users",
        sa.Column(
            "municipality_id",
            sa.Integer(),
            sa.ForeignKey(
                "municipalities.ibge_code",
                ondelete="SET NULL",
                name="fk_users_municipality_id_municipalities",
            ),
        ),
    )
    op.add_column(
        "listings",
        sa.Column(
            "municipality_id",
            sa.Integer(),
            sa.ForeignKey(
                "municipalities.ibge_code", name="fk_listings_municipality_id_municipalities"
            ),
        ),
    )
    op.create_index("ix_listings_municipality_id", "listings", ["municipality_id"])

    # Backfill existing rows whose free-text city matches a municipality of the same state.
    for table in ("users", "listings"):  # fixed table names, not input
        op.execute(
            f"UPDATE {table} t SET municipality_id = m.ibge_code, city = m.name "  # noqa: S608
            "FROM municipalities m WHERE t.state = m.state "
            "AND m.name_key = lower(regexp_replace(immutable_unaccent(t.city), '[''-]', ' ', 'g'))"
        )

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_notifications_user_id_users"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_notifications_user", "notifications", ["user_id", "id"])
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_index("ix_listings_municipality_id", table_name="listings")
    op.drop_column("listings", "municipality_id")
    op.drop_column("users", "municipality_id")
    op.drop_column("users", "locale")
    op.drop_table("municipalities")
    op.execute("DROP EXTENSION IF EXISTS earthdistance")
    op.execute("DROP EXTENSION IF EXISTS cube")
