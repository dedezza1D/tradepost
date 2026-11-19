"""Saved searches: a query and filters someone wants to follow, with alerts for new listings.

A job checks each search every few minutes for listings published since its last check and
sends one notification with the count. Alerts use full-text matching only, even when a model
is available: a vector search always has nearest neighbours, so it would alert on listings
that merely resemble the query.
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.catalog.models import ItemCondition, Listing
from tradepost.catalog.schemas import SearchParams
from tradepost.catalog.search import _filters, text_match
from tradepost.db import Base, new_id
from tradepost.deps import DbSession
from tradepost.errors import Conflict, NotFound
from tradepost.identity.sessions import CurrentUser
from tradepost.notifications.service import notify
from tradepost.security.ratelimit import rate_limit

MAX_PER_USER = 20
CHECK_EVERY = timedelta(minutes=10)
BATCH = 100


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    __table_args__ = (UniqueConstraint("user_id", "signature"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Hash of the normalized params: saving the same search twice keeps one.
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class SavedSearchIn(BaseModel):
    q: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=40)
    condition: ItemCondition | None = None
    state: str | None = Field(default=None, min_length=2, max_length=2)
    min_price: int | None = Field(default=None, ge=0)
    max_price: int | None = Field(default=None, ge=0)

    @field_validator("q")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        value = " ".join(value.split()) if value else None
        return value or None

    @model_validator(mode="after")
    def _specific(self) -> "SavedSearchIn":
        # Without words or a category, every new listing would match.
        if not self.q and not self.category:
            raise ValueError("A saved search needs words or a category.")
        return self

    def normalized(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class SavedSearchOut(BaseModel):
    id: uuid.UUID
    params: dict[str, Any]
    created_at: datetime


def signature(params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {k: (v.casefold() if k == "q" else v) for k, v in params.items()}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def new_matches(db: AsyncSession, search: SavedSearch) -> int:
    """Listings published since the last check that match the search, from other sellers."""
    params = SearchParams(**search.params)
    clauses = [
        *_filters(params, search.user_id),
        Listing.published_at > search.checked_at,
        Listing.seller_id != search.user_id,
    ]
    if params.q:
        clauses.append(text_match(params.q)[0])
    count = await db.scalar(select(func.count()).select_from(Listing).where(*clauses))
    return int(count or 0)


async def send_alerts(db: AsyncSession, now: datetime | None = None) -> int:
    """Job: check the searches whose turn has come and notify about new matches."""
    now = now or await db.scalar(select(func.now()))
    assert now is not None
    due = (
        await db.scalars(
            select(SavedSearch)
            .where(SavedSearch.checked_at <= now - CHECK_EVERY)
            .order_by(SavedSearch.checked_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    ).all()
    sent = 0
    for search in due:
        found = await new_matches(db, search)
        if found:
            await notify(
                db,
                search.user_id,
                "saved_search",
                {"search_id": str(search.id), "params": search.params, "count": found},
            )
            sent += 1
        search.checked_at = now
    return sent


router = APIRouter(prefix="/api/me/saved-searches", tags=["saved-searches"])


def _out(search: SavedSearch) -> SavedSearchOut:
    return SavedSearchOut(id=search.id, params=search.params, created_at=search.created_at)


@router.get("")
async def list_saved(user: CurrentUser, db: DbSession) -> list[SavedSearchOut]:
    rows = await db.scalars(
        select(SavedSearch)
        .where(SavedSearch.user_id == user.id)
        .order_by(SavedSearch.created_at.desc())
    )
    return [_out(s) for s in rows]


@router.post(
    "",
    dependencies=[Depends(rate_limit("saved_search", limit=60, window_seconds=3600))],
)
async def save(body: SavedSearchIn, user: CurrentUser, db: DbSession) -> SavedSearchOut:
    """Save a search; saving one that is already saved returns it unchanged."""
    params = body.normalized()
    sig = signature(params)
    existing = await db.scalar(
        select(SavedSearch).where(SavedSearch.user_id == user.id, SavedSearch.signature == sig)
    )
    if existing is not None:
        return _out(existing)
    count = await db.scalar(
        select(func.count()).select_from(SavedSearch).where(SavedSearch.user_id == user.id)
    )
    if (count or 0) >= MAX_PER_USER:
        raise Conflict(f"You can save up to {MAX_PER_USER} searches.", code="saved_search_limit")
    search = SavedSearch(user_id=user.id, params=params, signature=sig)
    db.add(search)
    await db.flush()
    await db.refresh(search)
    return _out(search)


@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(search_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    search = await db.get(SavedSearch, search_id)
    if search is None or search.user_id != user.id:
        raise NotFound("Saved search not found.")
    await db.delete(search)
