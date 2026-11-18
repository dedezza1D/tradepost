import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from tradepost.catalog import service
from tradepost.catalog.models import Category, Listing, ListingPhoto, ListingStatus
from tradepost.catalog.schemas import (
    CategoryOut,
    ListingCard,
    ListingCreate,
    ListingOut,
    ListingPage,
    ListingUpdate,
    PhotoOut,
    SearchParams,
)
from tradepost.catalog.search import search_listings
from tradepost.config import get_settings
from tradepost.deps import DbSession
from tradepost.identity.sessions import CurrentUser, OptionalPrincipal
from tradepost.media.images import InvalidImage
from tradepost.media.storage import Storage
from tradepost.security.ratelimit import rate_limit

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
    params: Annotated[SearchParams, Query()], db: DbSession, storage: StorageDep
) -> ListingPage:
    return await search_listings(db, storage, params)


@router.get("/listings/{listing_id}")
async def get_listing(
    listing_id: uuid.UUID, principal: OptionalPrincipal, db: DbSession, storage: StorageDep
) -> ListingOut:
    viewer = principal.user if principal else None
    listing = await service.get_visible_listing(db, listing_id, viewer)
    return await service.listing_out(db, storage, listing)


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
