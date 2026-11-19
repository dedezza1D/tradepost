"""Fill a local database with the evaluation catalog (about 160 listings, no photos).

Usage:
    uv run python -m scripts.seed_catalog

Meant for trying search on a development machine: the listings belong to a seller account that
cannot sign in, and running it again changes nothing. With TP_AI_PROVIDER=ollama the API's
background job embeds them within a few ticks, and semantic search and similar items light up.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from eval.search_dataset import catalog
from eval.search_eval import listing_id
from tradepost.catalog.models import ItemCondition, Listing, ListingStatus
from tradepost.config import get_settings
from tradepost.db import Database
from tradepost.identity.models import User

SELLER_EMAIL = "catalog-seller@seed.invalid"


async def main() -> None:
    database = Database(get_settings())
    async with database.sessionmaker() as db, db.begin():
        seller = await db.scalar(select(User).where(User.email == SELLER_EMAIL))
        if seller is None:
            seller = User(
                email=SELLER_EMAIL,
                password_hash="!",  # noqa: S106 - not a valid hash: the account cannot sign in
                display_name="Catálogo de exemplo",
                city="Recife",
                state="PE",
            )
            db.add(seller)
            await db.flush()
        added = 0
        for item in catalog():
            if await db.get(Listing, listing_id(item)) is not None:
                continue
            db.add(
                Listing(
                    id=listing_id(item),
                    seller_id=seller.id,
                    title=item.title,
                    description=item.description,
                    category_slug=item.category,
                    condition=ItemCondition.USED,
                    price_cents=item.price_cents,
                    stock=1,
                    city="Recife",
                    state="PE",
                    status=ListingStatus.ACTIVE,
                    published_at=datetime.now(UTC),
                )
            )
            added += 1
    await database.dispose()
    print(f"added {added} listings")


if __name__ == "__main__":
    asyncio.run(main())
