import asyncio
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import text

from tradepost.catalog.schemas import ListingPage
from tradepost.catalog.service import MEDIA_QUOTA_LOCK, StorageFull, image_slots, media_used
from tradepost.config import get_settings
from tradepost.deps import DbSession
from tradepost.identity.models import User
from tradepost.identity.sessions import AdminUser, CurrentUser, OptionalPrincipal
from tradepost.media.images import InvalidImage, process_image
from tradepost.media.storage import Storage
from tradepost.reviews.reputation import seller_reputation
from tradepost.reviews.service import seller_rating
from tradepost.security.ratelimit import rate_limit
from tradepost.social import service
from tradepost.social.models import ReportReason, ReportStatus, ReportTarget

router = APIRouter(prefix="/api", tags=["social"])


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


StorageDep = Annotated[Storage, Depends(get_storage)]
Cursor = Annotated[str | None, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=60)]


class UserSummaryOut(BaseModel):
    id: uuid.UUID
    display_name: str
    avatar_url: str | None
    city: str | None
    state: str | None
    follower_count: int


class UserPageOut(BaseModel):
    items: list[UserSummaryOut]
    next_cursor: str | None


class ReputationOut(BaseModel):
    level: str
    completed_sales: int
    cancel_rate: float
    late_rate: float
    rating_avg: float | None
    rating_count: int


class ProfileOut(BaseModel):
    id: uuid.UUID
    display_name: str
    bio: str | None
    avatar_url: str | None
    city: str | None
    state: str | None
    member_since: datetime
    rating_avg: float | None
    rating_count: int
    follower_count: int
    following_count: int
    listing_count: int
    is_me: bool
    following: bool
    blocked: bool
    reputation: ReputationOut


class FollowStateOut(BaseModel):
    following: bool
    follower_count: int


class SaveStateOut(BaseModel):
    saved: bool
    save_count: int


Details = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class ReportIn(BaseModel):
    target_type: ReportTarget
    target_id: uuid.UUID
    reason: ReportReason
    details: Details | None = None


class ReportOut(BaseModel):
    id: uuid.UUID
    target_type: ReportTarget
    target_id: uuid.UUID
    reason: ReportReason
    details: str | None
    status: ReportStatus
    created_at: datetime


class AdminReportOut(ReportOut):
    target_label: str
    reports_on_target: int
    triage_category: str | None
    triage_priority: int | None
    triage_reason: str | None


class ResolveIn(BaseModel):
    outcome: ReportStatus = Field(description="dismissed or actioned")


class ResolveOut(BaseModel):
    closed: int


def _summary(storage: Storage, user: User) -> UserSummaryOut:
    return UserSummaryOut(
        id=user.id,
        display_name=user.display_name,
        avatar_url=service.avatar_url(storage, user),
        city=user.city,
        state=user.state,
        follower_count=user.follower_count,
    )


# --- feed and profiles -----------------------------------------------------------------------


@router.get("/feed", dependencies=[Depends(rate_limit("feed", limit=120, window_seconds=60))])
async def feed(
    principal: OptionalPrincipal,
    db: DbSession,
    storage: StorageDep,
    tab: service.FeedTab = "trending",
    cursor: Cursor = None,
    limit: Limit = 24,
    near: Annotated[int | None, Query(ge=1_000_000, le=9_999_999)] = None,
    radius_km: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ListingPage:
    viewer = principal.user if principal else None
    return await service.feed(
        db, storage, viewer, tab, cursor=cursor, limit=limit, near=near, radius_km=radius_km
    )


@router.get("/users/{user_id}")
async def profile(
    user_id: uuid.UUID, principal: OptionalPrincipal, db: DbSession, storage: StorageDep
) -> ProfileOut:
    viewer = principal.user if principal else None
    user = await service.public_profile_user(db, user_id, viewer)
    rating_avg, rating_count = await seller_rating(db, user.id)
    rep = await seller_reputation(db, user.id)
    is_me = viewer is not None and viewer.id == user.id
    return ProfileOut(
        id=user.id,
        display_name=user.display_name,
        bio=user.bio,
        avatar_url=service.avatar_url(storage, user),
        city=user.city,
        state=user.state,
        member_since=user.created_at,
        rating_avg=rating_avg,
        rating_count=rating_count,
        follower_count=user.follower_count,
        following_count=user.following_count,
        listing_count=await service.active_listing_count(db, user.id),
        is_me=is_me,
        following=bool(viewer and not is_me and await service.is_following(db, viewer.id, user.id)),
        blocked=bool(viewer and not is_me and await service.has_blocked(db, viewer.id, user.id)),
        reputation=ReputationOut(
            level=rep.level,
            completed_sales=rep.completed_sales,
            cancel_rate=round(rep.cancel_rate, 3),
            late_rate=round(rep.late_rate, 3),
            rating_avg=rep.rating_avg,
            rating_count=rep.rating_count,
        ),
    )


@router.get("/users/{user_id}/listings")
async def profile_listings(
    user_id: uuid.UUID,
    principal: OptionalPrincipal,
    db: DbSession,
    storage: StorageDep,
    cursor: Cursor = None,
    limit: Limit = 24,
) -> ListingPage:
    viewer = principal.user if principal else None
    user = await service.public_profile_user(db, user_id, viewer)
    return await service.seller_listings(
        db, storage, user.id, viewer.id if viewer else None, cursor, limit
    )


# --- follows ---------------------------------------------------------------------------------

_follow_limit = Depends(rate_limit("follow", limit=120, window_seconds=3600))


@router.put("/users/{user_id}/follow", dependencies=[_follow_limit])
async def follow(user_id: uuid.UUID, user: CurrentUser, db: DbSession) -> FollowStateOut:
    await service.follow(db, user, user_id)
    target = await db.get(User, user_id)
    assert target is not None
    await db.refresh(target, ["follower_count"])
    return FollowStateOut(following=True, follower_count=target.follower_count)


@router.delete("/users/{user_id}/follow", dependencies=[_follow_limit])
async def unfollow(user_id: uuid.UUID, user: CurrentUser, db: DbSession) -> FollowStateOut:
    await service.unfollow(db, user, user_id)
    target = await db.get(User, user_id)
    count = 0
    if target is not None:
        await db.refresh(target, ["follower_count"])
        count = target.follower_count
    return FollowStateOut(following=False, follower_count=count)


@router.get("/me/following")
async def my_following(
    user: CurrentUser, db: DbSession, storage: StorageDep, cursor: Cursor = None, limit: Limit = 30
) -> UserPageOut:
    users, next_cursor = await service.following_users(db, user, cursor, limit)
    return UserPageOut(items=[_summary(storage, u) for u in users], next_cursor=next_cursor)


# --- saves -----------------------------------------------------------------------------------

_save_limit = Depends(rate_limit("save", limit=300, window_seconds=3600))


async def _save_state(db: DbSession, listing_id: uuid.UUID, saved: bool) -> SaveStateOut:
    count = (
        await db.execute(text("SELECT save_count FROM listings WHERE id = :id"), {"id": listing_id})
    ).scalar_one_or_none()
    return SaveStateOut(saved=saved, save_count=count or 0)


@router.put("/listings/{listing_id}/save", dependencies=[_save_limit])
async def save(listing_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SaveStateOut:
    await service.save_listing(db, user, listing_id)
    return await _save_state(db, listing_id, True)


@router.delete("/listings/{listing_id}/save", dependencies=[_save_limit])
async def unsave(listing_id: uuid.UUID, user: CurrentUser, db: DbSession) -> SaveStateOut:
    await service.unsave_listing(db, user, listing_id)
    return await _save_state(db, listing_id, False)


@router.get("/me/saved")
async def my_saved(
    user: CurrentUser, db: DbSession, storage: StorageDep, cursor: Cursor = None, limit: Limit = 24
) -> ListingPage:
    return await service.saved_listings(db, storage, user, cursor, limit)


# --- blocks and reports ----------------------------------------------------------------------

_block_limit = Depends(rate_limit("block", limit=60, window_seconds=3600))


@router.put(
    "/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_block_limit]
)
async def block(user_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.block(db, user, user_id)


@router.delete(
    "/users/{user_id}/block", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_block_limit]
)
async def unblock(user_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await service.unblock(db, user, user_id)


@router.get("/me/blocks")
async def my_blocks(user: CurrentUser, db: DbSession, storage: StorageDep) -> list[UserSummaryOut]:
    return [_summary(storage, u) for u in await service.blocked_users(db, user)]


@router.post(
    "/reports",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("report", limit=20, window_seconds=3600))],
)
async def create_report(body: ReportIn, user: CurrentUser, db: DbSession) -> ReportOut:
    entry = await service.report(
        db, user, body.target_type, body.target_id, body.reason, body.details
    )
    await db.flush()
    await db.refresh(entry)
    return ReportOut.model_validate(entry, from_attributes=True)


@router.get("/admin/reports")
async def admin_reports(admin: AdminUser, db: DbSession) -> list[AdminReportOut]:
    return [
        AdminReportOut(
            **ReportOut.model_validate(entry, from_attributes=True).model_dump(),
            triage_category=entry.triage_category,
            triage_priority=entry.triage_priority,
            triage_reason=entry.triage_reason,
            target_label=label,
            reports_on_target=count,
        )
        for entry, label, count in await service.open_reports(db)
    ]


@router.post("/admin/reports/{report_id}/resolve")
async def resolve_report(
    report_id: uuid.UUID, body: ResolveIn, admin: AdminUser, db: DbSession
) -> ResolveOut:
    return ResolveOut(closed=await service.resolve_report(db, admin, report_id, body.outcome))


# --- avatar ----------------------------------------------------------------------------------


@router.post(
    "/me/avatar",
    dependencies=[Depends(rate_limit("avatar", limit=20, window_seconds=3600))],
)
async def upload_avatar(
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
) -> UserSummaryOut:
    settings = get_settings()
    data = await file.read(settings.max_photo_bytes + 1)
    if len(data) > settings.max_photo_bytes:
        raise InvalidImage(f"Photos must be at most {settings.max_photo_bytes // 2**20} MB.")
    async with image_slots:
        processed = await asyncio.to_thread(
            process_image,
            data,
            max_bytes=settings.max_photo_bytes,
            max_pixels=settings.max_image_pixels,
        )
    # Only the small rendition is kept: avatars never show larger than a thumbnail.
    size = len(processed.thumb)
    await db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MEDIA_QUOTA_LOCK})
    if await media_used(db) - user.avatar_bytes + size > settings.media_quota_bytes:
        raise StorageFull("Photo storage for this demo is full. Try again later.")
    old_key = user.avatar_key
    key = f"avatars/{user.id}/{uuid.uuid4()}.webp"
    await storage.put(key, processed.thumb, "image/webp")
    user.avatar_key, user.avatar_bytes = key, size
    await db.flush()
    if old_key:
        await storage.delete(old_key)
    return _summary(storage, user)


@router.delete("/me/avatar", status_code=status.HTTP_204_NO_CONTENT)
async def delete_avatar(user: CurrentUser, db: DbSession, storage: StorageDep) -> None:
    old_key = user.avatar_key
    user.avatar_key, user.avatar_bytes = None, 0
    await db.flush()
    if old_key:
        await storage.delete(old_key)
