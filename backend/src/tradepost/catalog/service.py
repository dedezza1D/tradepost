import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from tradepost.catalog.models import (
    Category,
    Listing,
    ListingPhoto,
    ListingStatus,
    ListingVariant,
    ShippingMode,
)
from tradepost.catalog.schemas import (
    ListingCreate,
    ListingOut,
    ListingUpdate,
    PhotoOut,
    SellerOut,
    VariantIn,
    VariantOut,
)
from tradepost.config import Settings
from tradepost.errors import AppError, Conflict, Forbidden, NotFound
from tradepost.geo.service import resolve_city
from tradepost.identity.models import User
from tradepost.media.images import process_image
from tradepost.media.storage import Storage
from tradepost.social.blocks import is_blocked

# Image decoding is CPU and memory heavy; the free instance has 512 MB.
image_slots = asyncio.Semaphore(2)

MEDIA_QUOTA_LOCK = 7_301_002


class StorageFull(AppError):
    status_code = 507
    code = "storage_full"


PUBLIC_STATUSES = frozenset({ListingStatus.ACTIVE, ListingStatus.PAUSED, ListingStatus.SOLD_OUT})


async def media_used(db: AsyncSession) -> int:
    """Bytes stored for listing photos and avatars, counted against the media quota."""
    photos = select(func.coalesce(func.sum(ListingPhoto.bytes), 0)).scalar_subquery()
    avatars = select(func.coalesce(func.sum(User.avatar_bytes), 0)).scalar_subquery()
    return int((await db.execute(select(photos + avatars))).scalar_one())


def _check_package(listing: Listing) -> None:
    if listing.shipping_mode != ShippingMode.CALCULATED:
        return
    if None in (listing.weight_grams, listing.length_cm, listing.width_cm, listing.height_cm):
        raise AppError(
            "Calculated shipping needs the weight and size of the package.",
            code="package_required",
        )
    if listing.municipality_id is None:
        raise AppError("Calculated shipping needs a known city.", code="unknown_city")


async def _variants(db: AsyncSession, listing_id: uuid.UUID) -> list[ListingVariant]:
    rows = await db.execute(
        select(ListingVariant)
        .where(ListingVariant.listing_id == listing_id, ListingVariant.active.is_(True))
        .order_by(ListingVariant.position)
    )
    return list(rows.scalars())


async def set_variants(
    db: AsyncSession, listing: Listing, variants: list[VariantIn]
) -> list[ListingVariant]:
    """Replace the listing's options. Kept ids are updated, new ones created, missing ones
    retired (orders keep pointing at them). The listing's stock becomes their sum."""
    names = [v.name.casefold() for v in variants]
    if len(set(names)) != len(names):
        raise AppError("Two options have the same name.", code="duplicate_variant")
    current = {v.id: v for v in await _variants(db, listing.id)}
    keep: set[uuid.UUID] = set()
    for position, item in enumerate(variants):
        existing = current.get(item.id) if item.id else None
        if item.id is not None and existing is None:
            raise AppError("Unknown option.", code="unknown_variant")
        if existing is not None:
            existing.name, existing.stock, existing.position = item.name, item.stock, position
            keep.add(existing.id)
        else:
            db.add(
                ListingVariant(
                    listing_id=listing.id, name=item.name, stock=item.stock, position=position
                )
            )
    for variant in current.values():
        if variant.id not in keep:
            variant.active = False
    # Retire first, then insert: a renamed option may reuse a retired name.
    await db.flush()
    if variants:
        listing.stock = sum(v.stock for v in variants)
        _sync_stock_status(listing)
        await db.flush()
    return await _variants(db, listing.id)


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
    if viewer is not None and not is_owner and await is_blocked(db, viewer.id, listing.seller_id):
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
    city = await resolve_city(db, body.city, body.state)
    listing = Listing(
        seller_id=seller.id,
        status=ListingStatus.DRAFT,
        **body.model_dump(exclude={"city", "state"}, exclude_none=True),
        city=city.name,
        state=city.state,
        municipality_id=city.ibge_code,
    )
    _check_package(listing)
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
    old_price, old_stock = listing.price_cents, listing.stock
    if changes.get("stock") is not None and await _variants(db, listing.id):
        raise AppError(
            "Stock comes from the options; edit them instead.", code="stock_from_variants"
        )
    if "category_slug" in changes and changes["category_slug"] is not None:
        await _require_category(db, changes["category_slug"])
    if changes.get("city") or changes.get("state"):
        city = await resolve_city(
            db,
            changes.pop("city", None) or listing.city,
            changes.pop("state", None) or listing.state,
        )
        listing.city, listing.state, listing.municipality_id = city.name, city.state, city.ibge_code
    for field, value in changes.items():
        if value is not None:
            setattr(listing, field, value)
    _sync_stock_status(listing)
    _check_package(listing)
    try:
        await db.flush()
    except StaleDataError:
        raise Conflict(
            "The listing changed since you loaded it. Reload and try again.",
            code="stale_version",
        ) from None
    await db.refresh(listing)
    from tradepost.social.service import alert_low_stock, alert_price_drop

    if listing.price_cents < old_price:
        await alert_price_drop(db, listing.id, listing.title, old_price, listing.price_cents)
    if listing.stock == 1 and old_stock > 1:
        await alert_low_stock(db, listing.id, listing.title)
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
    owned = (
        await db.execute(
            select(func.count(ListingPhoto.id))
            .join(Listing, Listing.id == ListingPhoto.listing_id)
            .where(Listing.seller_id == listing.seller_id)
        )
    ).scalar_one()
    if owned >= settings.max_photos_per_user:
        raise AppError(
            f"An account can store at most {settings.max_photos_per_user} photos. "
            "Delete photos from old listings to add new ones.",
            code="photo_limit_reached",
        )
    async with image_slots:
        processed = await asyncio.to_thread(
            process_image,
            data,
            max_bytes=settings.max_photo_bytes,
            max_pixels=settings.max_image_pixels,
        )
    size = len(processed.full) + len(processed.thumb)
    # One upload at a time checks and claims space, so concurrent uploads cannot overshoot
    # the quota. The lock is released when the request's transaction ends.
    await db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MEDIA_QUOTA_LOCK})
    used = await media_used(db)
    if used + size > settings.media_quota_bytes:
        raise StorageFull("Photo storage for this demo is full. Try again later.")
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
        bytes=size,
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


async def reorder_photos(db: AsyncSession, listing: Listing, photo_ids: list[uuid.UUID]) -> None:
    """Set the photo order; the first one is the cover. ``photo_ids`` must list every photo of
    the listing exactly once."""
    current = {p.id: p for p in listing.photos}
    if len(photo_ids) != len(current) or set(photo_ids) != set(current):
        raise AppError("Send every photo of the listing exactly once.", code="bad_photo_order")
    # Positions are unique per listing: move them out of the way first, then set the new order.
    for offset, photo in enumerate(current.values()):
        photo.position = 1000 + offset
    await db.flush()
    for position, photo_id in enumerate(photo_ids):
        current[photo_id].position = position
    await db.flush()
    listing.photos.sort(key=lambda p: p.position)


def photo_out(storage: Storage, photo: ListingPhoto) -> PhotoOut:
    return PhotoOut(
        id=photo.id,
        position=photo.position,
        url=storage.url(photo.key_full),
        thumb_url=storage.url(photo.key_thumb),
        width=photo.width,
        height=photo.height,
    )


async def listing_out(
    db: AsyncSession, storage: Storage, listing: Listing, viewer_id: uuid.UUID | None = None
) -> ListingOut:
    seller = await db.get(User, listing.seller_id)
    if seller is None:  # pragma: no cover - FK guarantees it
        raise Forbidden("Seller not found.")
    from tradepost.reviews.reputation import seller_reputation
    from tradepost.reviews.service import seller_rating
    from tradepost.social.service import has_saved

    rating_avg, rating_count = await seller_rating(db, seller.id)
    return ListingOut(
        id=listing.id,
        seller=SellerOut(
            id=seller.id,
            display_name=seller.display_name,
            avatar_url=storage.url(seller.avatar_key) if seller.avatar_key else None,
            reputation=(await seller_reputation(db, seller.id)).level,
            rating_avg=rating_avg,
            rating_count=rating_count,
        ),
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
        municipality_id=listing.municipality_id,
        status=listing.status,
        version=listing.version,
        photos=[photo_out(storage, p) for p in listing.photos],
        created_at=listing.created_at,
        published_at=listing.published_at,
        save_count=listing.save_count,
        saved=viewer_id is not None and await has_saved(db, viewer_id, listing.id),
        variants=[
            VariantOut(id=v.id, name=v.name, stock=v.stock) for v in await _variants(db, listing.id)
        ],
        shipping_mode=listing.shipping_mode,
        weight_grams=listing.weight_grams,
        length_cm=listing.length_cm,
        width_cm=listing.width_cm,
        height_cm=listing.height_cm,
    )
