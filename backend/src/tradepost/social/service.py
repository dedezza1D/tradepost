"""Follows, saves, blocks, reports, the home feed and public profiles.

Counters (followers, following, saves) change in the same transaction as the row they count,
with atomic ``x = x + 1`` updates, so they never drift and need no background job.

The feed is computed when it is read ("pull"): the following tab joins ``follows`` with the
sellers' listings, which the (seller_id, created_at) index serves directly. Writing every new
listing into each follower's timeline ("push" / fan-out on write) only pays off with far more
followers and readers than this project has.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import (
    DateTime,
    Float,
    and_,
    delete,
    func,
    literal,
    select,
    text,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.catalog.models import Listing, ListingStatus
from tradepost.catalog.schemas import ListingCard, ListingPage, SearchParams
from tradepost.catalog.search import (
    MAX_RELEVANCE_OFFSET,
    InvalidCursor,
    _dt,
    _thumb_subquery,
    _uuid,
    decode_cursor,
    encode_cursor,
    search_listings,
    to_card,
    visible_to,
)
from tradepost.errors import AppError, Forbidden, NotFound
from tradepost.identity.models import User
from tradepost.media.storage import Storage
from tradepost.notifications.service import CHANNEL, notify
from tradepost.orders.models import Order, OrderItem
from tradepost.social.blocks import is_blocked
from tradepost.social.models import (
    Block,
    Follow,
    Report,
    ReportReason,
    ReportStatus,
    ReportTarget,
    Save,
)

# Ceilings that keep one account from filling the free database.
MAX_FOLLOWING = 1000
MAX_SAVES = 500
MAX_BLOCKS = 500

# Trending: a save counts 1 and a sale 3, both halving every ~2 days, over two weeks.
TRENDING_WINDOW = timedelta(days=14)
TRENDING_DECAY_SECONDS = 3 * 24 * 3600
SALE_WEIGHT = 3.0

FeedTab = Literal["following", "nearby", "trending"]


def avatar_url(storage: Storage, user: User) -> str | None:
    return storage.url(user.avatar_key) if user.avatar_key else None


async def _user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    return user


async def _count(db: AsyncSession, stmt: Any) -> int:
    return int((await db.execute(stmt)).scalar_one())


# --- follows ---------------------------------------------------------------------------------


async def follow(db: AsyncSession, user: User, target_id: uuid.UUID) -> None:
    if target_id == user.id:
        raise AppError("You cannot follow yourself.", code="follow_self")
    target = await _user(db, target_id)
    if await is_blocked(db, user.id, target.id):
        raise Forbidden("You cannot follow this user.", code="blocked")
    if user.following_count >= MAX_FOLLOWING:
        raise AppError(f"You can follow at most {MAX_FOLLOWING} people.", code="follow_limit")
    inserted = (
        await db.execute(
            insert(Follow)
            .values(follower_id=user.id, followee_id=target.id)
            .on_conflict_do_nothing()
            .returning(Follow.follower_id)
        )
    ).first()
    if inserted is None:
        return  # Already following: the request is idempotent.
    await _bump(db, user.id, following=1)
    await _bump(db, target.id, followers=1)
    await notify(
        db, target.id, "new_follower", {"user_id": str(user.id), "name": user.display_name}
    )


async def unfollow(db: AsyncSession, user: User, target_id: uuid.UUID) -> None:
    await _remove_follow(db, user.id, target_id)


async def _remove_follow(db: AsyncSession, follower: uuid.UUID, followee: uuid.UUID) -> None:
    deleted = (
        await db.execute(
            delete(Follow)
            .where(Follow.follower_id == follower, Follow.followee_id == followee)
            .returning(Follow.follower_id)
        )
    ).first()
    if deleted is not None:
        await _bump(db, follower, following=-1)
        await _bump(db, followee, followers=-1)


async def _bump(
    db: AsyncSession, user_id: uuid.UUID, *, followers: int = 0, following: int = 0
) -> None:
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            follower_count=User.follower_count + followers,
            following_count=User.following_count + following,
        )
    )


async def is_following(db: AsyncSession, follower: uuid.UUID, followee: uuid.UUID) -> bool:
    row = await db.get(Follow, (follower, followee))
    return row is not None


# --- saves -----------------------------------------------------------------------------------


async def save_listing(db: AsyncSession, user: User, listing_id: uuid.UUID) -> None:
    listing = await db.get(Listing, listing_id)
    if listing is None or listing.status not in (
        ListingStatus.ACTIVE,
        ListingStatus.PAUSED,
        ListingStatus.SOLD_OUT,
    ):
        raise NotFound("Listing not found.")
    if listing.seller_id == user.id:
        raise AppError("You cannot save your own listing.", code="save_own_listing")
    if await is_blocked(db, user.id, listing.seller_id):
        raise NotFound("Listing not found.")
    saved = await _count(db, select(func.count()).where(Save.user_id == user.id))
    if saved >= MAX_SAVES:
        raise AppError(f"You can save at most {MAX_SAVES} listings.", code="save_limit")
    inserted = (
        await db.execute(
            insert(Save)
            .values(user_id=user.id, listing_id=listing.id, price_cents_at_save=listing.price_cents)
            .on_conflict_do_nothing()
            .returning(Save.user_id)
        )
    ).first()
    if inserted is None:
        return
    await _bump_saves(db, listing.id, 1)
    await notify(
        db,
        listing.seller_id,
        "listing_saved",
        {"listing_id": str(listing.id), "title": listing.title, "name": user.display_name},
    )


async def unsave_listing(db: AsyncSession, user: User, listing_id: uuid.UUID) -> None:
    deleted = (
        await db.execute(
            delete(Save)
            .where(Save.user_id == user.id, Save.listing_id == listing_id)
            .returning(Save.listing_id)
        )
    ).first()
    if deleted is not None:
        await _bump_saves(db, listing_id, -1)


async def _bump_saves(db: AsyncSession, listing_id: uuid.UUID, delta: int) -> None:
    # Plain SQL on purpose: an ORM update would not bump ``version`` either, but this keeps
    # it obvious that saving never touches the seller's optimistic lock.
    await db.execute(
        text("UPDATE listings SET save_count = save_count + :d WHERE id = :id"),
        {"d": delta, "id": listing_id},
    )


async def mark_saved(
    db: AsyncSession, viewer_id: uuid.UUID | None, cards: list[ListingCard]
) -> None:
    if viewer_id is None or not cards:
        return
    saved = set(
        (
            await db.execute(
                select(Save.listing_id).where(
                    Save.user_id == viewer_id, Save.listing_id.in_([c.id for c in cards])
                )
            )
        ).scalars()
    )
    for card in cards:
        card.saved = card.id in saved


async def has_saved(db: AsyncSession, user_id: uuid.UUID, listing_id: uuid.UUID) -> bool:
    return await db.get(Save, (user_id, listing_id)) is not None


# Alerts to the people who saved a listing. Each statement marks the saves it alerts, writes
# the notifications and wakes the users' streams in one round trip.

_PRICE_DROP = text(
    """
    WITH hit AS (
        UPDATE saves SET alerted_price_cents = :price
         WHERE listing_id = :listing_id
           AND :price < LEAST(price_cents_at_save,
                              COALESCE(alerted_price_cents, price_cents_at_save))
        RETURNING user_id
    ), sent AS (
        INSERT INTO notifications (user_id, kind, payload)
        SELECT user_id, 'saved_price_drop', CAST(:payload AS jsonb) FROM hit
        RETURNING user_id
    )
    SELECT pg_notify(:channel, user_id::text) FROM sent
    """
)

_LOW_STOCK = text(
    """
    WITH hit AS (
        UPDATE saves SET low_stock_alerted = true
         WHERE listing_id = :listing_id
           AND NOT low_stock_alerted
           AND user_id IS DISTINCT FROM CAST(:exclude AS uuid)
        RETURNING user_id
    ), sent AS (
        INSERT INTO notifications (user_id, kind, payload)
        SELECT user_id, 'saved_low_stock', CAST(:payload AS jsonb) FROM hit
        RETURNING user_id
    )
    SELECT pg_notify(:channel, user_id::text) FROM sent
    """
)


async def alert_price_drop(
    db: AsyncSession, listing_id: uuid.UUID, title: str, old_cents: int, new_cents: int
) -> int:
    payload = {
        "listing_id": str(listing_id),
        "title": title,
        "old_price_cents": old_cents,
        "price_cents": new_cents,
    }
    rows = await db.execute(
        _PRICE_DROP,
        {
            "listing_id": listing_id,
            "price": new_cents,
            "payload": json.dumps(payload),
            "channel": CHANNEL,
        },
    )
    return len(rows.all())


async def alert_low_stock(
    db: AsyncSession, listing_id: uuid.UUID, title: str, exclude: uuid.UUID | None = None
) -> int:
    payload = {"listing_id": str(listing_id), "title": title}
    rows = await db.execute(
        _LOW_STOCK,
        {
            "listing_id": listing_id,
            "exclude": str(exclude) if exclude else None,
            "payload": json.dumps(payload),
            "channel": CHANNEL,
        },
    )
    return len(rows.all())


# --- blocks ----------------------------------------------------------------------------------


async def block(db: AsyncSession, user: User, target_id: uuid.UUID) -> None:
    if target_id == user.id:
        raise AppError("You cannot block yourself.", code="block_self")
    target = await _user(db, target_id)
    blocks = await _count(db, select(func.count()).where(Block.blocker_id == user.id))
    if blocks >= MAX_BLOCKS:
        raise AppError(f"You can block at most {MAX_BLOCKS} people.", code="block_limit")
    inserted = (
        await db.execute(
            insert(Block)
            .values(blocker_id=user.id, blocked_id=target.id)
            .on_conflict_do_nothing()
            .returning(Block.blocker_id)
        )
    ).first()
    if inserted is None:
        return
    # Cut every tie in both directions: follows, and saves of each other's listings.
    await _remove_follow(db, user.id, target.id)
    await _remove_follow(db, target.id, user.id)
    for saver, seller in ((user.id, target.id), (target.id, user.id)):
        removed = (
            await db.execute(
                delete(Save)
                .where(
                    Save.user_id == saver,
                    Save.listing_id.in_(select(Listing.id).where(Listing.seller_id == seller)),
                )
                .returning(Save.listing_id)
            )
        ).scalars()
        for listing_id in removed:
            await _bump_saves(db, listing_id, -1)


async def unblock(db: AsyncSession, user: User, target_id: uuid.UUID) -> None:
    await db.execute(
        delete(Block).where(Block.blocker_id == user.id, Block.blocked_id == target_id)
    )


async def has_blocked(db: AsyncSession, blocker: uuid.UUID, blocked: uuid.UUID) -> bool:
    return await db.get(Block, (blocker, blocked)) is not None


# --- reports and moderation ------------------------------------------------------------------


async def report(
    db: AsyncSession,
    user: User,
    target_type: ReportTarget,
    target_id: uuid.UUID,
    reason: ReportReason,
    details: str | None,
) -> Report:
    if target_type == ReportTarget.LISTING:
        listing = await db.get(Listing, target_id)
        if listing is None or listing.status in (
            ListingStatus.DRAFT,
            ListingStatus.REMOVED,
            ListingStatus.IN_REVIEW,
        ):
            raise NotFound("Listing not found.")
        if listing.seller_id == user.id:
            raise AppError("You cannot report your own listing.", code="report_self")
    else:
        await _user(db, target_id)
        if target_id == user.id:
            raise AppError("You cannot report yourself.", code="report_self")
    entry = Report(
        reporter_id=user.id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        details=details or None,
    )
    try:
        async with db.begin_nested():
            db.add(entry)
    except IntegrityError:
        # This person already has an open report on the target: keep that one.
        existing = (
            await db.execute(
                select(Report).where(
                    Report.reporter_id == user.id,
                    Report.target_type == target_type,
                    Report.target_id == target_id,
                    Report.status == ReportStatus.OPEN,
                )
            )
        ).scalar_one()
        return existing
    return entry


async def open_reports(db: AsyncSession, limit: int = 100) -> list[tuple[Report, str, int]]:
    """Open reports, most urgent first (the triage job's suggestion, then age), with a label
    for the target and how many people reported it."""
    reports = list(
        (
            await db.execute(
                select(Report)
                .where(Report.status == ReportStatus.OPEN)
                .order_by(Report.triage_priority.asc().nulls_last(), Report.created_at, Report.id)
                .limit(limit)
            )
        ).scalars()
    )
    counts: dict[tuple[str, uuid.UUID], int] = {}
    for target_type, target_id, n in await db.execute(
        select(Report.target_type, Report.target_id, func.count())
        .where(Report.status == ReportStatus.OPEN)
        .group_by(Report.target_type, Report.target_id)
    ):
        counts[(target_type, target_id)] = n
    out = []
    for entry in reports:
        if entry.target_type == ReportTarget.LISTING:
            listing = await db.get(Listing, entry.target_id)
            label = listing.title if listing else "?"
        else:
            target = await db.get(User, entry.target_id)
            label = target.display_name if target else "?"
        out.append((entry, label, counts.get((entry.target_type, entry.target_id), 1)))
    return out


async def resolve_report(
    db: AsyncSession, admin: User, report_id: uuid.UUID, outcome: ReportStatus
) -> int:
    """Close every open report on the same target. Acting on a listing report removes the
    listing. Returns how many reports were closed."""
    if outcome == ReportStatus.OPEN:
        raise AppError("Choose dismissed or actioned.", code="bad_outcome")
    entry = await db.get(Report, report_id)
    if entry is None:
        raise NotFound("Report not found.")
    closed = (
        await db.execute(
            update(Report)
            .where(
                Report.target_type == entry.target_type,
                Report.target_id == entry.target_id,
                Report.status == ReportStatus.OPEN,
            )
            .values(status=outcome, resolved_at=func.now(), resolved_by=admin.id)
            .returning(Report.id)
        )
    ).all()
    if outcome == ReportStatus.ACTIONED and entry.target_type == ReportTarget.LISTING:
        listing = await db.get(Listing, entry.target_id)
        if listing is not None and listing.status != ListingStatus.REMOVED:
            listing.status = ListingStatus.REMOVED
            await notify(
                db,
                listing.seller_id,
                "listing_removed",
                {"listing_id": str(listing.id), "title": listing.title},
            )
    return len(closed)


# --- feed ------------------------------------------------------------------------------------


async def feed(
    db: AsyncSession,
    storage: Storage,
    viewer: User | None,
    tab: FeedTab,
    *,
    cursor: str | None,
    limit: int,
    near: int | None,
    radius_km: int,
) -> ListingPage:
    viewer_id = viewer.id if viewer else None
    if tab == "nearby":
        origin = near or (viewer.municipality_id if viewer else None)
        if origin is None:
            raise AppError("Choose a city to see listings near you.", code="city_required")
        page = await search_listings(
            db,
            storage,
            SearchParams(
                near=origin, radius_km=radius_km, sort="nearest", cursor=cursor, limit=limit
            ),
            viewer_id,
        )
    elif tab == "following":
        if viewer is None:
            raise AppError("Sign in to see who you follow.", code="sign_in_required")
        page = await _following(db, storage, viewer.id, cursor, limit)
    else:
        page = await _trending(db, storage, viewer_id, cursor, limit)
    await mark_saved(db, viewer_id, page.items)
    return page


async def _following(
    db: AsyncSession, storage: Storage, viewer_id: uuid.UUID, cursor: str | None, limit: int
) -> ListingPage:
    thumb = _thumb_subquery().label("thumb")
    stmt = (
        select(Listing, thumb)
        .join(
            Follow, and_(Follow.followee_id == Listing.seller_id, Follow.follower_id == viewer_id)
        )
        .where(*visible_to(viewer_id))
    )
    if cursor:
        data = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Listing.published_at, Listing.id)
            < tuple_(_dt(data.get("p")), _uuid(data.get("i")))
        )
    rows = (
        await db.execute(
            stmt.order_by(Listing.published_at.desc(), Listing.id.desc()).limit(limit + 1)
        )
    ).all()
    items = [to_card(listing, key, None, storage) for listing, key in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last: Listing = rows[limit - 1][0]
        assert last.published_at is not None
        next_cursor = encode_cursor({"p": last.published_at.isoformat(), "i": str(last.id)})
    return ListingPage(items=items, next_cursor=next_cursor)


async def _trending(
    db: AsyncSession,
    storage: Storage,
    viewer_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> ListingPage:
    # The cursor pins the clock: every page scores the same signals at the same instant, so
    # pages neither repeat nor skip listings while new saves arrive.
    data = decode_cursor(cursor) if cursor else {}
    as_of = _dt(data["t"]) if "t" in data else datetime.now(UTC)
    offset = int(data.get("o", 0))
    if offset < 0 or offset > MAX_RELEVANCE_OFFSET:
        raise InvalidCursor("Invalid page cursor.")

    now = literal(as_of, DateTime(timezone=True))
    since = as_of - TRENDING_WINDOW

    def decay(at: Any) -> Any:
        return func.exp(-func.extract("epoch", now - at) / TRENDING_DECAY_SECONDS)

    signals = union_all(
        select(Save.listing_id.label("listing_id"), decay(Save.created_at).label("w")).where(
            Save.created_at > since, Save.created_at <= as_of
        ),
        select(
            OrderItem.listing_id.label("listing_id"),
            (literal(SALE_WEIGHT, Float) * decay(Order.paid_at)).label("w"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(Order.paid_at > since, Order.paid_at <= as_of),
    ).subquery()
    scores = (
        select(signals.c.listing_id, func.sum(signals.c.w).label("score"))
        .group_by(signals.c.listing_id)
        .subquery()
    )
    thumb = _thumb_subquery().label("thumb")
    stmt = (
        select(Listing, thumb)
        .outerjoin(scores, scores.c.listing_id == Listing.id)
        .where(*visible_to(viewer_id), Listing.published_at <= as_of)
        .order_by(
            func.coalesce(scores.c.score, 0).desc(),
            Listing.published_at.desc(),
            Listing.id.desc(),
        )
        .offset(offset)
        .limit(limit + 1)
    )
    rows = (await db.execute(stmt)).all()
    items = [to_card(listing, key, None, storage) for listing, key in rows[:limit]]
    next_cursor = None
    if len(rows) > limit and offset + limit <= MAX_RELEVANCE_OFFSET:
        next_cursor = encode_cursor({"t": as_of.isoformat(), "o": offset + limit})
    return ListingPage(items=items, next_cursor=next_cursor)


# --- profile pages ---------------------------------------------------------------------------


async def public_profile_user(db: AsyncSession, user_id: uuid.UUID, viewer: User | None) -> User:
    user = await _user(db, user_id)
    # Someone who blocked the viewer disappears for them.
    if viewer is not None and viewer.id != user.id and await has_blocked(db, user.id, viewer.id):
        raise NotFound("User not found.")
    return user


async def active_listing_count(db: AsyncSession, seller_id: uuid.UUID) -> int:
    return await _count(
        db,
        select(func.count()).where(
            Listing.seller_id == seller_id, Listing.status == ListingStatus.ACTIVE
        ),
    )


async def seller_listings(
    db: AsyncSession,
    storage: Storage,
    seller_id: uuid.UUID,
    viewer_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> ListingPage:
    thumb = _thumb_subquery().label("thumb")
    stmt = select(Listing, thumb).where(Listing.seller_id == seller_id, *visible_to(viewer_id))
    if cursor:
        data = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Listing.published_at, Listing.id)
            < tuple_(_dt(data.get("p")), _uuid(data.get("i")))
        )
    rows = (
        await db.execute(
            stmt.order_by(Listing.published_at.desc(), Listing.id.desc()).limit(limit + 1)
        )
    ).all()
    items = [to_card(listing, key, None, storage) for listing, key in rows[:limit]]
    await mark_saved(db, viewer_id, items)
    next_cursor = None
    if len(rows) > limit:
        last: Listing = rows[limit - 1][0]
        assert last.published_at is not None
        next_cursor = encode_cursor({"p": last.published_at.isoformat(), "i": str(last.id)})
    return ListingPage(items=items, next_cursor=next_cursor)


async def saved_listings(
    db: AsyncSession, storage: Storage, user: User, cursor: str | None, limit: int
) -> ListingPage:
    thumb = _thumb_subquery().label("thumb")
    stmt = (
        select(Listing, thumb, Save.created_at)
        .join(Save, and_(Save.listing_id == Listing.id, Save.user_id == user.id))
        .where(
            Listing.status.in_([ListingStatus.ACTIVE, ListingStatus.PAUSED, ListingStatus.SOLD_OUT])
        )
    )
    if cursor:
        data = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Save.created_at, Save.listing_id)
            < tuple_(_dt(data.get("s")), _uuid(data.get("i")))
        )
    rows = (
        await db.execute(
            stmt.order_by(Save.created_at.desc(), Save.listing_id.desc()).limit(limit + 1)
        )
    ).all()
    items = [to_card(listing, key, None, storage) for listing, key, _ in rows[:limit]]
    for card in items:
        card.saved = True
    next_cursor = None
    if len(rows) > limit:
        listing, _, saved_at = rows[limit - 1]
        next_cursor = encode_cursor({"s": saved_at.isoformat(), "i": str(listing.id)})
    return ListingPage(items=items, next_cursor=next_cursor)


async def following_users(
    db: AsyncSession, user: User, cursor: str | None, limit: int
) -> tuple[list[User], str | None]:
    stmt = select(User, Follow.created_at).join(
        Follow, and_(Follow.followee_id == User.id, Follow.follower_id == user.id)
    )
    if cursor:
        data = decode_cursor(cursor)
        stmt = stmt.where(
            tuple_(Follow.created_at, Follow.followee_id)
            < tuple_(_dt(data.get("f")), _uuid(data.get("i")))
        )
    rows = (
        await db.execute(
            stmt.order_by(Follow.created_at.desc(), Follow.followee_id.desc()).limit(limit + 1)
        )
    ).all()
    next_cursor = None
    if len(rows) > limit:
        last, followed_at = rows[limit - 1]
        next_cursor = encode_cursor({"f": followed_at.isoformat(), "i": str(last.id)})
    return [u for u, _ in rows[:limit]], next_cursor


async def blocked_users(db: AsyncSession, user: User) -> list[User]:
    return list(
        (
            await db.execute(
                select(User)
                .join(Block, and_(Block.blocked_id == User.id, Block.blocker_id == user.id))
                .order_by(Block.created_at.desc())
                .limit(MAX_BLOCKS)
            )
        ).scalars()
    )
