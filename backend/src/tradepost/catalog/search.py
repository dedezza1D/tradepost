"""Listing search: Portuguese full-text search plus trigram matching for typos.

Results for newest/price orders are paginated with a keyset cursor (stable under inserts and
cheap at any depth). Relevance order has no natural key, so it uses an offset cursor capped at
a few pages, which is what people browse anyway.
"""

import base64
import binascii
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.catalog.models import Listing, ListingPhoto, ListingStatus
from tradepost.catalog.schemas import ListingCard, ListingPage, SearchParams
from tradepost.errors import AppError
from tradepost.media.storage import Storage

MAX_RELEVANCE_OFFSET = 600


class InvalidCursor(AppError):
    code = "invalid_cursor"


def encode_cursor(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, ValueError):
        raise InvalidCursor("Invalid page cursor.") from None
    if not isinstance(data, dict):
        raise InvalidCursor("Invalid page cursor.")
    return data


def _thumb_subquery() -> Any:
    return (
        select(ListingPhoto.key_thumb)
        .where(ListingPhoto.listing_id == Listing.id)
        .order_by(ListingPhoto.position)
        .limit(1)
        .scalar_subquery()
    )


def _filters(params: SearchParams) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [Listing.status == ListingStatus.ACTIVE]
    if params.category:
        clauses.append(Listing.category_slug == params.category)
    if params.condition:
        clauses.append(Listing.condition == params.condition)
    if params.state:
        clauses.append(Listing.state == params.state)
    if params.min_price is not None:
        clauses.append(Listing.price_cents >= params.min_price)
    if params.max_price is not None:
        clauses.append(Listing.price_cents <= params.max_price)
    return clauses


async def search_listings(db: AsyncSession, storage: Storage, params: SearchParams) -> ListingPage:
    query = (params.q or "").strip()
    sort = params.sort if (params.sort != "relevance" or query) else "newest"
    cursor = decode_cursor(params.cursor) if params.cursor else None

    thumb = _thumb_subquery().label("thumb")
    stmt: Select[Any] = select(Listing, thumb).where(*_filters(params))

    if query:
        tsquery = func.websearch_to_tsquery("portuguese", func.immutable_unaccent(query))
        title_norm = func.immutable_unaccent(func.lower(Listing.title))
        query_norm = func.immutable_unaccent(func.lower(literal(query)))
        stmt = stmt.where(
            or_(Listing.search_vector.op("@@")(tsquery), query_norm.op("<%")(title_norm))
        )
        rank = func.ts_rank(Listing.search_vector, tsquery) + func.word_similarity(
            query_norm, title_norm
        )
    else:
        rank = literal(0.0)

    limit = params.limit
    offset = 0
    if sort == "relevance":
        offset = int(cursor.get("o", 0)) if cursor else 0
        if offset < 0 or offset > MAX_RELEVANCE_OFFSET:
            raise InvalidCursor("Invalid page cursor.")
        stmt = stmt.order_by(rank.desc(), Listing.id).offset(offset)
    elif sort == "newest":
        if cursor:
            stmt = stmt.where(
                tuple_(Listing.published_at, Listing.id)
                < tuple_(_dt(cursor.get("p")), _uuid(cursor.get("i")))
            )
        stmt = stmt.order_by(Listing.published_at.desc(), Listing.id.desc())
    else:
        ascending = sort == "price_asc"
        if cursor:
            key = tuple_(Listing.price_cents, Listing.id)
            bound = tuple_(_int(cursor.get("v")), _uuid(cursor.get("i")))
            stmt = stmt.where(and_(key > bound if ascending else key < bound))
        stmt = stmt.order_by(
            Listing.price_cents.asc() if ascending else Listing.price_cents.desc(),
            Listing.id.asc() if ascending else Listing.id.desc(),
        )

    rows = (await db.execute(stmt.limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        ListingCard(
            id=listing.id,
            title=listing.title,
            price_cents=listing.price_cents,
            currency=listing.currency,
            condition=listing.condition,
            city=listing.city,
            state=listing.state,
            status=listing.status,
            thumb_url=storage.url(thumb_key) if thumb_key else None,
            published_at=listing.published_at,
        )
        for listing, thumb_key in rows
    ]

    next_cursor = None
    if has_more and rows:
        last: Listing = rows[-1][0]
        if sort == "relevance":
            if offset + limit <= MAX_RELEVANCE_OFFSET:
                next_cursor = encode_cursor({"o": offset + limit})
        elif sort == "newest":
            assert last.published_at is not None
            next_cursor = encode_cursor({"p": last.published_at.isoformat(), "i": str(last.id)})
        else:
            next_cursor = encode_cursor({"v": last.price_cents, "i": str(last.id)})
    return ListingPage(items=items, next_cursor=next_cursor)


def _dt(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise InvalidCursor("Invalid page cursor.") from None


def _uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise InvalidCursor("Invalid page cursor.") from None


def _int(value: Any) -> int:
    if not isinstance(value, int):
        raise InvalidCursor("Invalid page cursor.")
    return value
