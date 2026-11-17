"""Identity, sessions, rate limiting and catalog.

Revision ID: 0001
Revises:
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIES = [
    ("electronics", "Electronics"),
    ("phones", "Phones & Tablets"),
    ("computers", "Computers"),
    ("games", "Video Games"),
    ("home", "Home & Kitchen"),
    ("furniture", "Furniture"),
    ("appliances", "Appliances"),
    ("fashion", "Clothing & Accessories"),
    ("shoes", "Shoes"),
    ("beauty", "Beauty"),
    ("sports", "Sports & Outdoors"),
    ("bikes", "Bikes"),
    ("toys", "Toys"),
    ("baby", "Baby"),
    ("books", "Books"),
    ("music", "Musical Instruments"),
    ("tools", "Tools & DIY"),
    ("auto-parts", "Auto Parts"),
    ("pets", "Pet Supplies"),
    ("collectibles", "Collectibles"),
    ("other", "Other"),
]

SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('portuguese', immutable_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('portuguese', immutable_unaccent(coalesce(description, ''))), 'B')"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # unaccent() is STABLE, so it cannot back an index or a generated column. Pinning the
    # dictionary makes the wrapper safe to declare IMMUTABLE.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION immutable_unaccent(text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
        """
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(60), nullable=False),
        sa.Column("city", sa.String(80)),
        sa.Column("state", sa.String(2)),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_sessions_user_id_users"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("user_agent", sa.String(300)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_id_active", "sessions", ["user_id", "revoked_at"])

    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("window_start", sa.BigInteger(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )

    categories = op.create_table(
        "categories",
        sa.Column("slug", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
    )
    op.bulk_insert(
        categories,
        [{"slug": s, "name": n, "position": i} for i, (s, n) in enumerate(CATEGORIES)],
    )

    item_condition = postgresql.ENUM("new", "used", name="item_condition")
    listing_status = postgresql.ENUM(
        "draft", "active", "paused", "sold_out", "removed", name="listing_status"
    )

    op.create_table(
        "listings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "seller_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_listings_seller_id_users"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "category_slug",
            sa.String(40),
            sa.ForeignKey("categories.slug", name="fk_listings_category_slug_categories"),
            nullable=False,
        ),
        sa.Column("condition", item_condition, nullable=False),
        sa.Column("price_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BRL"),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.Column("city", sa.String(80), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("shipping_cents", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", listing_status, nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR_SQL, persisted=True),
            nullable=False,
        ),
        sa.CheckConstraint("price_cents > 0", name="ck_listings_price_positive"),
        sa.CheckConstraint("stock >= 0", name="ck_listings_stock_non_negative"),
        sa.CheckConstraint("shipping_cents >= 0", name="ck_listings_shipping_non_negative"),
    )
    op.create_index(
        "ix_listings_search_vector", "listings", ["search_vector"], postgresql_using="gin"
    )
    op.execute(
        "CREATE INDEX ix_listings_title_trgm ON listings "
        "USING gin (immutable_unaccent(lower(title)) gin_trgm_ops)"
    )
    op.create_index("ix_listings_status_published", "listings", ["status", "published_at", "id"])
    op.create_index("ix_listings_status_price", "listings", ["status", "price_cents", "id"])
    op.create_index("ix_listings_seller", "listings", ["seller_id", "created_at"])

    op.create_table(
        "listing_photos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey(
                "listings.id", ondelete="CASCADE", name="fk_listing_photos_listing_id_listings"
            ),
            nullable=False,
        ),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("key_full", sa.String(200), nullable=False),
        sa.Column("key_thumb", sa.String(200), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("listing_id", "position", name="uq_listing_photos_listing_id"),
    )


def downgrade() -> None:
    op.drop_table("listing_photos")
    op.drop_table("listings")
    op.execute("DROP TYPE IF EXISTS listing_status")
    op.execute("DROP TYPE IF EXISTS item_condition")
    op.drop_table("categories")
    op.drop_table("rate_limit_buckets")
    op.drop_table("sessions")
    op.drop_table("users")
    op.execute("DROP FUNCTION IF EXISTS immutable_unaccent(text)")
