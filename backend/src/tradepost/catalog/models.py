import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tradepost.db import Base, TimestampMixin, new_id


class ListingStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    SOLD_OUT = "sold_out"
    REMOVED = "removed"


class ItemCondition(enum.StrEnum):
    NEW = "new"
    USED = "used"


class Category(Base):
    __tablename__ = "categories"

    slug: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)


SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('portuguese', immutable_unaccent(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('portuguese', immutable_unaccent(coalesce(description, ''))), 'B')"
)


class Listing(TimestampMixin, Base):
    __tablename__ = "listings"
    __table_args__ = (
        CheckConstraint("price_cents > 0", name="price_positive"),
        CheckConstraint("stock >= 0", name="stock_non_negative"),
        Index("ix_listings_search_vector", "search_vector", postgresql_using="gin"),
        # The trigram index on the unaccented title is created in the migration (expression
        # index with an operator class).
        Index("ix_listings_status_published", "status", "published_at", "id"),
        Index("ix_listings_status_price", "status", "price_cents", "id"),
        Index("ix_listings_seller", "seller_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category_slug: Mapped[str] = mapped_column(ForeignKey("categories.slug"), nullable=False)
    condition: Mapped[ItemCondition] = mapped_column(
        Enum(ItemCondition, name="item_condition", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="BRL")
    stock: Mapped[int] = mapped_column(Integer, nullable=False)
    city: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(2), nullable=False)
    municipality_id: Mapped[int | None] = mapped_column(
        ForeignKey("municipalities.ibge_code"), index=True
    )
    shipping_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[ListingStatus] = mapped_column(
        Enum(ListingStatus, name="listing_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=ListingStatus.DRAFT,
    )
    # Optimistic locking: an edit must name the version it was based on.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True), nullable=False
    )

    __mapper_args__ = {"version_id_col": version}

    photos: Mapped[list["ListingPhoto"]] = relationship(
        order_by="ListingPhoto.position", lazy="selectin", cascade="all, delete-orphan"
    )


class ListingPhoto(TimestampMixin, Base):
    __tablename__ = "listing_photos"
    __table_args__ = (UniqueConstraint("listing_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    key_full: Mapped[str] = mapped_column(String(200), nullable=False)
    key_thumb: Mapped[str] = mapped_column(String(200), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    # Stored size of both renditions, counted against the storage quota.
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
