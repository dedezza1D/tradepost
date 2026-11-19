"""Discovery: similar listings and search suggestions.

Similar listings come from the listing's embedding when a model is available (nearest listings
by meaning, under the same distance cutoff as search). Without one, as on the live demo, they
are listings of the same category ranked by how close their titles are, which needs no model.

Suggestions complete what someone is typing in the search box from the titles of active
listings, using the trigram index that search already has. They never call a model: they run
on every few keystrokes.
"""

import uuid

from sqlalchemy import ColumnElement, Float, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.ai.client import AI
from tradepost.catalog.models import Listing
from tradepost.catalog.schemas import ListingCard
from tradepost.catalog.search import _thumb_subquery, title_norm, to_card, visible_to
from tradepost.catalog.semantic import ListingEmbedding
from tradepost.errors import NotFound
from tradepost.media.storage import Storage

SIMILAR_LIMIT = 8
SUGGESTIONS = 6


async def similar_listings(
    db: AsyncSession,
    storage: Storage,
    listing_id: uuid.UUID,
    viewer_id: uuid.UUID | None,
    ai: AI | None,
    limit: int = SIMILAR_LIMIT,
) -> list[ListingCard]:
    listing = await db.get(Listing, listing_id)
    if listing is None:
        raise NotFound("Listing not found.")
    thumb = _thumb_subquery().label("thumb")
    others = [*visible_to(viewer_id), Listing.id != listing_id]

    if ai is not None and await ai.available(db):
        model = ai.settings.ai_embed_model
        own = (
            select(ListingEmbedding.embedding)
            .where(ListingEmbedding.listing_id == listing_id, ListingEmbedding.model == model)
            .scalar_subquery()
        )
        distance = ListingEmbedding.embedding.op("<=>", return_type=Float())(own)
        rows = (
            await db.execute(
                select(Listing, thumb)
                .join(
                    ListingEmbedding,
                    (ListingEmbedding.listing_id == Listing.id) & (ListingEmbedding.model == model),
                )
                .where(*others, distance <= ai.settings.search_max_distance)
                .order_by(distance)
                .limit(limit)
            )
        ).all()
        if rows:
            return [to_card(row[0], row[1], None, storage) for row in rows]
        # Not embedded yet (the job runs every few seconds): fall through to the titles.

    closeness = func.word_similarity(
        func.immutable_unaccent(func.lower(literal(listing.title))), title_norm()
    )
    rows = (
        await db.execute(
            select(Listing, thumb)
            .where(*others, Listing.category_slug == listing.category_slug)
            .order_by(closeness.desc(), Listing.published_at.desc().nulls_last())
            .limit(limit)
        )
    ).all()
    return [to_card(row[0], row[1], None, storage) for row in rows]


async def suggestions(db: AsyncSession, prefix: str, limit: int = SUGGESTIONS) -> list[str]:
    """Distinct listing titles that contain what was typed or come close to it."""
    typed = func.immutable_unaccent(func.lower(literal(prefix)))
    matches: ColumnElement[bool] = typed.op("<%")(title_norm())
    # Substring test for short prefixes the trigram test misses; LIKE wildcards are dropped.
    plain = prefix.replace("%", "").replace("_", "").strip()
    if plain:
        matches = matches | title_norm().contains(func.immutable_unaccent(func.lower(plain)))
    closeness = func.word_similarity(typed, title_norm())
    rows = await db.execute(
        select(Listing.title)
        .where(*visible_to(None), matches)
        .order_by(closeness.desc(), Listing.save_count.desc())
        .limit(limit * 4)
    )
    seen: set[str] = set()
    out: list[str] = []
    for (title,) in rows:
        key = title.casefold().strip()
        if key not in seen:
            seen.add(key)
            out.append(title)
        if len(out) == limit:
            break
    return out
