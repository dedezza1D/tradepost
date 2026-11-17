import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from tradepost.catalog.models import Category, Listing, ListingPhoto, ListingStatus
from tradepost.catalog.schemas import (
    ListingCreate,
    ListingOut,
    ListingUpdate,
    PhotoOut,
    SellerOut,
)
from tradepost.config import Settings
from tradepost.errors import AppError, Conflict, Forbidden, NotFound
from tradepost.identity.models import User
from tradepost.media.images import process_image
from tradepost.media.storage import Storage

# Image decoding is CPU and memory heavy; the free instance has 512 MB.
_image_slots = asyncio.Semaphore(2)

PUBLIC_STATUSES = frozenset({ListingStatus.ACTIVE, ListingStatus.PAUSED, ListingStatus.SOLD_OUT})


async def _require_category(db: AsyncSession, slug: str) -> None:
    if await db.get(Category, slug) is None:
        raise AppError("Unknown category.", code="unknown_category")


async def get_listing(db: AsyncSession, listing_id: uuid.UUID) -> Listing:
    listing = await db.get(Listing, listing_id)
    if listing is None:
        raise NotFound("Listing not found.")
    return listing


async def get_visible_listing(
    db: AsyncSession, listing_id: uuid.UUID, viewer: User | None
) -> Listing:
    listing = await get_listing(db, listing_id)
    is_owner = viewer is not None and (viewer.id == listing.seller_id or viewer.is_admin)
    if listing.status not in PUBLIC_STATUSES and not is_owner:
        raise NotFound("Listing not found.")
    return listing


async def get_owned_listing(db: AsyncSession, listing_id: uuid.UUID, user: User) -> Listing:
    listing = await get_listing(db, listing_id)
    if listing.seller_id != user.id:
        # Same answer as a missing listing: do not reveal other sellers' drafts.
        raise NotFound("Listing not found.")
    if listing.status == ListingStatus.REMOVED:
        raise NotFound("Listing not found.")
    return listing


async def create_listing(db: AsyncSession, seller: User, body: ListingCreate) -> Listing:
    await _require_category(db, body.category_slug)
    listing = Listing(seller_id=seller.id, status=ListingStatus.DRAFT, **body.model_dump())
    db.add(listing)
    await db.flush()
    await db.refresh(listing)
    return listing


def _sync_stock_status(listing: Listing) -> None:
    if listing.status == ListingStatus.ACTIVE and listing.stock == 0:
        listing.status = ListingStatus.SOLD_OUT
    elif listing.status == ListingStatus.SOLD_OUT and listing.stock > 0:
        listing.status = ListingStatus.ACTIVE


async def update_listing(db: AsyncSession, listing: Listing, body: ListingUpdate) -> Listing:
    if body.version != listing.version:
        raise Conflict(
            "The listing changed since you loaded it. Reload and try again.",
            code="stale_version",
            current_version=listing.version,
        )
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if "category_slug" in changes and changes["category_slug"] is not None:
        await _require_category(db, changes["category_slug"])
    for field, value in changes.items():
        if value is not None:
            setattr(listing, field, value)
    _sync_stock_status(listing)
    try:
        await db.flush()
    except StaleDataError:
        raise Conflict(
            "The listing changed since you loaded it. Reload and try again.",
            code="stale_version",
        ) from None
    await db.refresh(listing)
    return listing


async def publish_listing(db: AsyncSession, listing: Listing) -> Listing:
    if not listing.photos:
        raise AppError("Add at least one photo before publishing.", code="photo_required")
    if listing.stock == 0:
        raise AppError("Set the stock before publishing.", code="stock_required")
    listing.status = ListingStatus.ACTIVE
    if listing.published_at is None:
        listing.published_at = datetime.now(UTC)
    await db.flush()
    return listing


async def pause_listing(db: AsyncSession, listing: Listing) -> Listing:
    if listing.status not in (ListingStatus.ACTIVE, ListingStatus.SOLD_OUT):
        raise AppError("Only published listings can be paused.", code="invalid_status")
    listing.status = ListingStatus.PAUSED
    await db.flush()
    return listing


async def remove_listing(db: AsyncSession, listing: Listing) -> None:
    # Soft delete: orders keep pointing at the listing.
    listing.status = ListingStatus.REMOVED
    await db.flush()


async def add_photo(
    db: AsyncSession, storage: Storage, settings: Settings, listing: Listing, data: bytes
) -> ListingPhoto:
    if len(listing.photos) >= settings.max_photos_per_listing:
        raise AppError(
            f"A listing can have at most {settings.max_photos_per_listing} photos.",
            code="too_many_photos",
        )
    async with _image_slots:
        processed = await asyncio.to_thread(
            process_image,
            data,
            max_bytes=settings.max_photo_bytes,
            max_pixels=settings.max_image_pixels,
        )
    photo_id = uuid.uuid4()
    key_full = f"listings/{listing.id}/{photo_id}.webp"
    key_thumb = f"listings/{listing.id}/{photo_id}_t.webp"
    await storage.put(key_full, processed.full, "image/webp")
    await storage.put(key_thumb, processed.thumb, "image/webp")
    next_position = (
        await db.execute(
            select(func.coalesce(func.max(ListingPhoto.position), -1) + 1).where(
                ListingPhoto.listing_id == listing.id
            )
        )
    ).scalar_one()
    photo = ListingPhoto(
        id=photo_id,
        listing_id=listing.id,
        position=next_position,
        key_full=key_full,
        key_thumb=key_thumb,
        width=processed.width,
        height=processed.height,
    )
    listing.photos.append(photo)
    try:
        await db.flush()
    except Exception:
        await storage.delete(key_full)
        await storage.delete(key_thumb)
        raise
    return photo


async def delete_photo(
    db: AsyncSession, storage: Storage, listing: Listing, photo_id: uuid.UUID
) -> None:
    photo = next((p for p in listing.photos if p.id == photo_id), None)
    if photo is None:
        raise NotFound("Photo not found.")
    if listing.status == ListingStatus.ACTIVE and len(listing.photos) == 1:
        raise AppError("A published listing needs at least one photo.", code="photo_required")
    listing.photos.remove(photo)
    await db.flush()
    await storage.delete(photo.key_full)
    await storage.delete(photo.key_thumb)


def photo_out(storage: Storage, photo: ListingPhoto) -> PhotoOut:
    return PhotoOut(
        id=photo.id,
        position=photo.position,
        url=storage.url(photo.key_full),
        thumb_url=storage.url(photo.key_thumb),
        width=photo.width,
        height=photo.height,
    )


async def listing_out(db: AsyncSession, storage: Storage, listing: Listing) -> ListingOut:
    seller = await db.get(User, listing.seller_id)
    if seller is None:  # pragma: no cover - FK guarantees it
        raise Forbidden("Seller not found.")
    return ListingOut(
        id=listing.id,
        seller=SellerOut(id=seller.id, display_name=seller.display_name),
        title=listing.title,
        description=listing.description,
        category_slug=listing.category_slug,
        condition=listing.condition,
        price_cents=listing.price_cents,
        currency=listing.currency,
        shipping_cents=listing.shipping_cents,
        stock=listing.stock,
        city=listing.city,
        state=listing.state,
        status=listing.status,
        version=listing.version,
        photos=[photo_out(storage, p) for p in listing.photos],
        created_at=listing.created_at,
        published_at=listing.published_at,
    )
