"""Load the public demo catalog: listings with freely licensed photos from Wikimedia Commons.

Usage (with the target database and storage settings in the environment):

    uv run python -m eval.listing_photos               # fetch the photos into eval/.cache
    uv run python -m scripts.seed_demo_catalog
    uv run python -m scripts.seed_demo_catalog --retire-placeholders

Listings come from scripts/demo_catalog.json and belong to a seller account that cannot sign in.
Each description ends with the photo's author, license and source, as the licenses require.
Running it again adds only what is missing. ``--retire-placeholders`` removes the earlier demo
listings whose title ends in "(anúncio de demonstração)".
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, update

from eval.listing_photos import PHOTOS
from tradepost.catalog import service
from tradepost.catalog.models import ItemCondition, Listing, ListingStatus
from tradepost.config import get_settings
from tradepost.db import Database
from tradepost.geo.models import Municipality
from tradepost.identity.models import User
from tradepost.media.storage import build_storage

CATALOG = Path(__file__).with_name("demo_catalog.json")
SELLER_EMAIL = "demo-catalog@tradepost.invalid"
SELLER_NAME = "Catálogo de demonstração"
PLACEHOLDER_SUFFIX = "(anúncio de demonstração)"


def description(entry: dict[str, object]) -> str:
    credit = entry["credit"]
    assert isinstance(credit, dict)
    author = " ".join(str(credit["author"]).split())[:120]
    return (
        f"{entry['description']}\n\nAnúncio de demonstração do tradepost: não está à venda.\n\n"
        f"Foto: {author}, {credit['license']}, via Wikimedia Commons ({credit['source']})."
    )


async def main(retire: bool) -> None:
    settings = get_settings()
    database = Database(settings)
    storage = build_storage(settings)
    entries: list[dict[str, object]] = json.loads(CATALOG.read_text(encoding="utf-8"))
    added = retired = 0
    async with database.sessionmaker() as db, db.begin():
        seller = await db.scalar(select(User).where(User.email == SELLER_EMAIL))
        if seller is None:
            seller = User(
                email=SELLER_EMAIL,
                password_hash="!",  # noqa: S106 - not a valid hash: the account cannot sign in
                display_name=SELLER_NAME,
                city="São Paulo",
                state="SP",
            )
            db.add(seller)
            await db.flush()
        existing = set(
            await db.scalars(select(Listing.title).where(Listing.seller_id == seller.id))
        )
        for index, entry in enumerate(entries):
            title = str(entry["title"])
            if title in existing:
                continue
            town = await db.scalar(
                select(Municipality).where(
                    func.lower(Municipality.name) == str(entry["city"]).lower(),
                    Municipality.state == entry["state"],
                )
            )
            listing = Listing(
                seller_id=seller.id,
                title=title,
                description=description(entry),
                category_slug=str(entry["category"]),
                condition=ItemCondition(str(entry["condition"])),
                price_cents=int(str(entry["price_cents"])),
                stock=1,
                city=str(entry["city"]),
                state=str(entry["state"]),
                municipality_id=town.ibge_code if town else None,
                status=ListingStatus.ACTIVE,
                # Spread over the last ten days, so "newest" mixes kinds instead of the last loaded.
                published_at=datetime.now(UTC) - timedelta(hours=(index * 97) % 240),
            )
            db.add(listing)
            await db.flush()
            await db.refresh(listing, ["photos"])
            photo = await asyncio.to_thread((PHOTOS / str(entry["photo"])).read_bytes)
            await service.add_photo(db, storage, settings, listing, photo)
            added += 1
        if retire:
            result = await db.execute(
                update(Listing)
                .where(
                    Listing.title.endswith(PLACEHOLDER_SUFFIX),
                    Listing.status != ListingStatus.REMOVED,
                )
                .values(status=ListingStatus.REMOVED)
            )
            retired = int(result.rowcount or 0)  # type: ignore[attr-defined]
    await database.dispose()
    print(f"added {added} listings, retired {retired} placeholders")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--retire-placeholders", action="store_true")
    asyncio.run(main(parser.parse_args().retire_placeholders))
