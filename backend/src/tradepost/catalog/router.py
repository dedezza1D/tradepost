import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from tradepost.catalog import service
from tradepost.catalog.models import Category, Listing, ListingPhoto, ListingStatus, ShippingMode
from tradepost.catalog.schemas import (
    CategoryOut,
    ListingCard,
    ListingCreate,
    ListingOut,
    ListingPage,
    ListingUpdate,
    PhotoOut,
    SearchParams,
    ShippingOption,
    ShippingQuoteOut,
    VariantsIn,
)
from tradepost.catalog.search import search_listings
from tradepost.config import get_settings
from tradepost.deps import DbSession
from tradepost.errors import AppError, NotFound
from tradepost.geo.models import Municipality
from tradepost.identity.sessions import CurrentUser, OptionalPrincipal
from tradepost.media.images import InvalidImage, share_preview
from tradepost.media.storage import Storage
from tradepost.security.ratelimit import rate_limit
from tradepost.shipping import Parcel, distance_km, quoter

router = APIRouter(prefix="/api", tags=["catalog"])


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


StorageDep = Annotated[Storage, Depends(get_storage)]


@router.get("/categories")
async def list_categories(db: DbSession) -> list[CategoryOut]:
    rows = (await db.execute(select(Category).order_by(Category.position))).scalars()
    return [CategoryOut.model_validate(c) for c in rows]


@router.get(
    "/listings",
    dependencies=[Depends(rate_limit("search", limit=120, window_seconds=60))],
)
async def search(
    request: Request,
    params: Annotated[SearchParams, Query()],
    principal: OptionalPrincipal,
    db: DbSession,
    storage: StorageDep,
) -> ListingPage:
    from tradepost.catalog.semantic import hybrid_search
    from tradepost.social.service import mark_saved

    viewer_id = principal.user.id if principal else None
    page = await hybrid_search(
        db, storage, params, viewer_id, request.app.state.ai
    ) or await search_listings(db, storage, params, viewer_id)
    await mark_saved(db, viewer_id, page.items)
    return page


class Suggestions(BaseModel):
    queries: list[str]


@router.get(
    "/search/suggest",
    dependencies=[Depends(rate_limit("suggest", limit=240, window_seconds=60))],
)
async def suggest(
    q: Annotated[str, Query(min_length=2, max_length=100)], db: DbSession
) -> Suggestions:
    from tradepost.catalog.discovery import suggestions

    return Suggestions(queries=await suggestions(db, q.strip()))


@router.get("/listings/{listing_id}/similar")
async def similar(
    listing_id: uuid.UUID,
    request: Request,
    principal: OptionalPrincipal,
    db: DbSession,
    storage: StorageDep,
) -> list[ListingCard]:
    from tradepost.catalog.discovery import similar_listings

    viewer_id = principal.user.id if principal else None
    return await similar_listings(db, storage, listing_id, viewer_id, request.app.state.ai)


@router.get("/listings/{listing_id}")
async def get_listing(
    listing_id: uuid.UUID, principal: OptionalPrincipal, db: DbSession, storage: StorageDep
) -> ListingOut:
    viewer = principal.user if principal else None
    listing = await service.get_visible_listing(db, listing_id, viewer)
    return await service.listing_out(db, storage, listing, viewer.id if viewer else None)


@router.get(
    "/listings/{listing_id}/og.jpg",
    dependencies=[Depends(rate_limit("og_image", limit=60, window_seconds=60))],
    response_class=Response,
)
async def share_image(listing_id: uuid.UUID, db: DbSession, storage: StorageDep) -> Response:
    """Link-preview image of a public listing, rendered on demand. The edge caches it, so
    the API renders each one rarely."""
    listing = await service.get_visible_listing(db, listing_id, None)
    data = await storage.get(listing.photos[0].key_full) if listing.photos else None
    if data is None:
        raise NotFound("No photo.")
    async with service.image_slots:
        jpeg = await asyncio.to_thread(share_preview, data)
    return Response(
        jpeg, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"}
    )


@router.post(
    "/listings",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("listing_write", limit=60, window_seconds=3600))],
)
async def create_listing(
    body: ListingCreate, user: CurrentUser, db: DbSession, storage: StorageDep
) -> ListingOut:
    listing = await service.create_listing(db, user, body)
    return await service.listing_out(db, storage, listing)


@router.patch("/listings/{listing_id}")
async def update_listing(
    listing_id: uuid.UUID,
    body: ListingUpdate,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
) -> ListingOut:
    listing = await service.get_owned_listing(db, listing_id, user)
    listing = await service.update_listing(db, listing, body)
    return await service.listing_out(db, storage, listing)


@router.post("/listings/{listing_id}/publish")
async def publish_listing(
    listing_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> ListingOut:
    listing = await service.get_owned_listing(db, listing_id, user)
    listing = await service.publish_listing(db, listing)
    return await service.listing_out(db, storage, listing)


@router.post("/listings/{listing_id}/pause")
async def pause_listing(
    listing_id: uuid.UUID, user: CurrentUser, db: DbSession, storage: StorageDep
) -> ListingOut:
    listing = await service.get_owned_listing(db, listing_id, user)
    listing = await service.pause_listing(db, listing)
    return await service.listing_out(db, storage, listing)


@router.delete("/listings/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_listing(listing_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    listing = await service.get_owned_listing(db, listing_id, user)
    await service.remove_listing(db, listing)


@router.post(
    "/listings/{listing_id}/photos",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("photo_upload", limit=60, window_seconds=3600))],
)
async def upload_photo(
    listing_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
) -> PhotoOut:
    settings = get_settings()
    listing = await service.get_owned_listing(db, listing_id, user)
    # Read one byte past the limit to detect oversize files without buffering all of them.
    data = await file.read(settings.max_photo_bytes + 1)
    if len(data) > settings.max_photo_bytes:
        raise InvalidImage(f"Photos must be at most {settings.max_photo_bytes // 2**20} MB.")
    photo: ListingPhoto = await service.add_photo(db, storage, settings, listing, data)
    return service.photo_out(storage, photo)


@router.put("/listings/{listing_id}/variants")
async def set_variants(
    listing_id: uuid.UUID, body: VariantsIn, user: CurrentUser, db: DbSession, storage: StorageDep
) -> ListingOut:
    """Replace the listing's options (size, color...), each with its own stock."""
    listing = await service.get_owned_listing(db, listing_id, user)
    await service.set_variants(db, listing, body.variants)
    await db.refresh(listing)
    return await service.listing_out(db, storage, listing)


@router.get(
    "/listings/{listing_id}/shipping",
    dependencies=[Depends(rate_limit("shipping_quote", limit=120, window_seconds=60))],
)
async def shipping_quote(
    listing_id: uuid.UUID,
    db: DbSession,
    to: Annotated[int | None, Query(ge=1_000_000, le=9_999_999)] = None,
    quantity: Annotated[int, Query(ge=1, le=10)] = 1,
) -> ShippingQuoteOut:
    """Shipping options for a listing to a city (IBGE code)."""
    listing = await service.get_visible_listing(db, listing_id, None)
    if listing.shipping_mode != ShippingMode.CALCULATED:
        return ShippingQuoteOut(
            mode=listing.shipping_mode,
            destination=None,
            options=[
                ShippingOption(
                    service="seller", carrier="", price_cents=listing.shipping_cents, days=None
                )
            ],
        )
    if to is None:
        return ShippingQuoteOut(mode=listing.shipping_mode, destination=None, options=[])
    origin = (
        await db.get(Municipality, listing.municipality_id) if listing.municipality_id else None
    )
    dest = await db.get(Municipality, to)
    if dest is None or origin is None or listing.weight_grams is None:
        raise AppError("Unknown city.", code="unknown_city")
    parcel = Parcel(
        listing.weight_grams, listing.length_cm or 1, listing.width_cm or 1, listing.height_cm or 1
    )
    km = distance_km(origin.lat, origin.lng, dest.lat, dest.lng)
    return ShippingQuoteOut(
        mode=listing.shipping_mode,
        destination=f"{dest.name} - {dest.state}",
        options=[
            ShippingOption(
                service=q.service, carrier=q.carrier, price_cents=q.price_cents, days=q.days
            )
            for q in quoter.quote(km, parcel, quantity)
        ],
    )


class PhotoOrderIn(BaseModel):
    photo_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


@router.put("/listings/{listing_id}/photos/order")
async def reorder_photos(
    listing_id: uuid.UUID, body: PhotoOrderIn, user: CurrentUser, db: DbSession, storage: StorageDep
) -> ListingOut:
    listing = await service.get_owned_listing(db, listing_id, user)
    await service.reorder_photos(db, listing, body.photo_ids)
    return await service.listing_out(db, storage, listing)


@router.delete("/listings/{listing_id}/photos/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_photo(
    listing_id: uuid.UUID,
    photo_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
) -> None:
    listing = await service.get_owned_listing(db, listing_id, user)
    await service.delete_photo(db, storage, listing, photo_id)


@router.get("/me/listings")
async def my_listings(user: CurrentUser, db: DbSession, storage: StorageDep) -> list[ListingCard]:
    listings = (
        await db.execute(
            select(Listing)
            .where(Listing.seller_id == user.id, Listing.status != ListingStatus.REMOVED)
            .order_by(Listing.created_at.desc())
            .limit(200)
        )
    ).scalars()
    return [
        ListingCard(
            id=item.id,
            title=item.title,
            price_cents=item.price_cents,
            currency=item.currency,
            condition=item.condition,
            city=item.city,
            state=item.state,
            status=item.status,
            thumb_url=storage.url(item.photos[0].key_thumb) if item.photos else None,
            published_at=item.published_at,
        )
        for item in listings
    ]
