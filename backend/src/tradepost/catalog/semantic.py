"""Semantic search: listing embeddings in PostgreSQL (pgvector) fused with full-text search.

Full-text search finds the words people typed; embeddings find what they meant ("magrela" for
a bike, "fritadeira sem óleo" for an air fryer). Search takes the best candidates of each and
merges them with reciprocal rank fusion (RRF): every list adds ``weight / (k + rank)`` for the
listings it returned. RRF needs no score calibration between the two very different scales,
which is why it is the usual first choice for hybrid search. The weights were tuned on the dev
half of the evaluation set (``eval/search_eval.py``).

Without a model (the live demo) nothing here runs and search is full-text only. Vectors are
searched exactly (no index): a scan over a few thousand listings takes milliseconds; an HNSW
index would come in past tens of thousands.
"""

import hashlib
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, cast, func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from tradepost.ai.client import AI, AIUnavailable
from tradepost.catalog.models import Listing, ListingStatus
from tradepost.catalog.schemas import ListingPage, SearchParams
from tradepost.db import Base
from tradepost.media.storage import Storage

FEATURE_INDEX = "search_index"
FEATURE_QUERY = "search_query"
BATCH = 16
CANDIDATES = 50

# Some embedding models are trained with task prefixes and work worse without them.
PREFIXES: dict[str, tuple[str, str]] = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
}


class Vector(UserDefinedType[list[float]]):
    """pgvector's ``vector`` without a fixed dimension (one table holds several models)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "vector"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Sequence[float] | None) -> str | None:
            return None if value is None else "[" + ",".join(f"{x:.6f}" for x in value) + "]"

        return process

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: str | None) -> list[float] | None:
            return None if value is None else [float(x) for x in value.strip("[]").split(",")]

        return process


class ListingEmbedding(Base):
    __tablename__ = "listing_embeddings"

    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(80), primary_key=True)
    # Hash of the embedded text: an edit to the title or description re-embeds the listing.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def document_text(title: str, description: str) -> str:
    return f"{title}. {description}"[:2000]


def _hash(text_: str) -> str:
    return hashlib.sha256(text_.encode()).hexdigest()


def prefixed(model: str, texts: Iterable[str], *, query: bool) -> list[str]:
    doc, q = PREFIXES.get(model.split(":")[0], ("", ""))
    prefix = q if query else doc
    return [prefix + t for t in texts]


async def embed_pending(db: AsyncSession, ai: AI, limit: int = 64) -> int:
    """Job: embed active listings that are new or changed since their last embedding."""
    if not await ai.available(db):
        return 0
    model = ai.settings.ai_embed_model
    rows = (
        await db.execute(
            select(Listing.id, Listing.title, Listing.description, ListingEmbedding.content_hash)
            .outerjoin(
                ListingEmbedding,
                (ListingEmbedding.listing_id == Listing.id) & (ListingEmbedding.model == model),
            )
            .where(
                Listing.status.in_(
                    [ListingStatus.ACTIVE, ListingStatus.PAUSED, ListingStatus.SOLD_OUT]
                )
            )
            .order_by(Listing.updated_at.desc())
        )
    ).all()
    pending = []
    for listing_id, title, description, current in rows:
        content = document_text(title, description)
        if current != _hash(content):
            pending.append((listing_id, content))
        if len(pending) >= limit:
            break
    done = 0
    for start in range(0, len(pending), BATCH):
        batch = pending[start : start + BATCH]
        try:
            vectors = await ai.embed(
                db, FEATURE_INDEX, prefixed(model, [c for _, c in batch], query=False)
            )
        except AIUnavailable:
            break
        for (listing_id, content), vector in zip(batch, vectors, strict=True):
            await db.execute(
                text(
                    """
                    INSERT INTO listing_embeddings (listing_id, model, content_hash, embedding)
                    VALUES (:id, :model, :hash, CAST(CAST(:vec AS text) AS vector))
                    ON CONFLICT (listing_id, model) DO UPDATE SET
                        content_hash = excluded.content_hash, embedding = excluded.embedding,
                        updated_at = now()
                    """
                ),
                {
                    "id": listing_id,
                    "model": model,
                    "hash": _hash(content),
                    "vec": as_literal(vector),
                },
            )
            done += 1
    return done


async def query_vector(db: AsyncSession, ai: AI, query: str) -> list[float] | None:
    """The query's embedding, or None when no model can answer (search falls back to text)."""
    if not await ai.available(db):
        return None
    try:
        vectors = await ai.embed(
            db, FEATURE_QUERY, prefixed(ai.settings.ai_embed_model, [query], query=True)
        )
    except AIUnavailable:
        return None
    return vectors[0]


def as_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


def distance(vector: list[float]) -> Any:
    """Cosine distance from a query vector, for ``ORDER BY``. The vector travels as text and is
    cast in SQL: the database driver has no codec for pgvector's type."""
    return ListingEmbedding.embedding.op("<=>", return_type=Float())(
        cast(literal(as_literal(vector), String), Vector())
    )


async def hybrid_search(
    db: AsyncSession,
    storage: Storage,
    params: SearchParams,
    viewer_id: uuid.UUID | None,
    ai: AI | None,
) -> ListingPage | None:
    """Relevance search fusing full-text and vector rankings, or None when it does not apply
    (no query, another sort, a distance filter, or no model available)."""
    from tradepost.catalog.search import (
        MAX_RELEVANCE_OFFSET,
        InvalidCursor,
        _filters,
        _thumb_subquery,
        decode_cursor,
        encode_cursor,
        search_listings,
        to_card,
    )

    query = (params.q or "").strip()
    if ai is None or not query or params.sort != "relevance" or params.near is not None:
        return None
    vector = await query_vector(db, ai, query)
    if vector is None:
        return None
    settings = ai.settings
    offset = int(decode_cursor(params.cursor).get("o", 0)) if params.cursor else 0
    if offset < 0 or offset > MAX_RELEVANCE_OFFSET:
        raise InvalidCursor("Invalid page cursor.")

    text_page = await search_listings(
        db, storage, params.model_copy(update={"cursor": None, "limit": CANDIDATES}), viewer_id
    )
    text_ids = [card.id for card in text_page.items]
    vector_ids = list(
        (
            await db.execute(
                select(Listing.id)
                .join(
                    ListingEmbedding,
                    (ListingEmbedding.listing_id == Listing.id)
                    & (ListingEmbedding.model == ai.settings.ai_embed_model),
                )
                .where(*_filters(params, viewer_id))
                .where(distance(vector) <= settings.search_max_distance)
                .order_by(distance(vector))
                .limit(CANDIDATES)
            )
        ).scalars()
    )
    fused = fuse(
        [(text_ids, settings.search_text_weight), (vector_ids, settings.search_vector_weight)],
        settings.search_rrf_k,
    )
    page_ids = fused[offset : offset + params.limit]
    thumb = _thumb_subquery().label("thumb")
    rows = {
        listing.id: (listing, key)
        for listing, key in (
            await db.execute(select(Listing, thumb).where(Listing.id.in_(page_ids)))
        ).all()
    }
    items = [to_card(rows[i][0], rows[i][1], None, storage) for i in page_ids if i in rows]
    more = offset + params.limit < len(fused) and offset + params.limit <= MAX_RELEVANCE_OFFSET
    return ListingPage(
        items=items,
        next_cursor=encode_cursor({"o": offset + params.limit}) if more else None,
        approximate=bool(items) and not text_ids,
    )


def fuse(rankings: list[tuple[list[uuid.UUID], float]], k: int) -> list[uuid.UUID]:
    """Reciprocal rank fusion of ranked id lists, each with a weight."""
    scores: dict[uuid.UUID, float] = {}
    for ids, weight in rankings:
        for rank, listing_id in enumerate(ids, start=1):
            scores[listing_id] = scores.get(listing_id, 0.0) + weight / (k + rank)
    return sorted(scores, key=lambda i: scores[i], reverse=True)
