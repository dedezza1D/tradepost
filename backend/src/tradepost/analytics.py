"""Seller analytics: how each listing performs, and how the shop as a whole does.

Everything except views is read from where it already lives (saves, questions, conversations,
orders), so the dashboard cannot disagree with the orders and the chat. Views are the one new
measure: a listing page opened by someone other than its seller counts once per visitor per day.
They are counted in memory and added up by day into ``listing_daily_views`` by a job, the way
the request metrics are; the visitor key (the account, or a hash of address and browser) never
reaches the database. Days are Brasília days.
"""

import csv
import hashlib
import io
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import Date, ForeignKey, Integer, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base
from tradepost.deps import DbSession
from tradepost.identity.sessions import CurrentUser
from tradepost.security.client import client_ip

TZ = ZoneInfo("America/Sao_Paulo")
KEEP_DAYS = 400
# Link previews and crawlers open listing pages too; they are not visitors.
BOTS = re.compile(r"bot|crawl|spider|slurp|preview|facebookexternalhit|whatsapp|telegram", re.I)
# A listing seen by more distinct visitors in a day than this stops remembering them (memory
# stays bounded); further views still count, possibly twice for the same person.
MAX_KEYS = 5000


class ListingDailyViews(Base):
    __tablename__ = "listing_daily_views"

    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    views: Mapped[int] = mapped_column(Integer, nullable=False)


def today() -> date:
    return datetime.now(TZ).date()


class ViewCounter:
    def __init__(self) -> None:
        self._seen: dict[tuple[date, uuid.UUID], set[str]] = defaultdict(set)
        self._counts: dict[tuple[date, uuid.UUID], int] = defaultdict(int)

    def record(self, listing_id: uuid.UUID, visitor: str, day: date | None = None) -> bool:
        key = (day or today(), listing_id)
        seen = self._seen[key]
        if visitor in seen:
            return False
        if len(seen) < MAX_KEYS:
            seen.add(visitor)
        self._counts[key] += 1
        return True

    def drain(self) -> dict[tuple[date, uuid.UUID], int]:
        counts, self._counts = self._counts, defaultdict(int)
        # Yesterday's visitors are forgotten once the day has turned.
        current = today()
        self._seen = defaultdict(set, {k: v for k, v in self._seen.items() if k[0] >= current})
        return dict(counts)


views = ViewCounter()


def visitor_key(request: Request, user_id: uuid.UUID | None) -> str | None:
    """Who is looking, for counting each visitor once a day; None for robots."""
    agent = request.headers.get("user-agent", "")
    if BOTS.search(agent):
        return None
    if user_id is not None:
        return f"u:{user_id}"
    raw = f"{client_ip(request)}|{agent}"
    return "a:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


async def flush_views(db: AsyncSession, source: ViewCounter | None = None) -> int:
    """Job: add the counted views to the daily table."""
    counts = (source or views).drain()
    for (day, listing_id), n in counts.items():
        await db.execute(
            text(
                """
                INSERT INTO listing_daily_views (listing_id, day, views)
                SELECT :id, :day, :n WHERE EXISTS (SELECT 1 FROM listings WHERE id = :id)
                ON CONFLICT (listing_id, day) DO UPDATE
                    SET views = listing_daily_views.views + excluded.views
                """
            ),
            {"id": listing_id, "day": day, "n": n},
        )
    return len(counts)


async def purge_views(db: AsyncSession) -> int:
    result = await db.execute(
        text("DELETE FROM listing_daily_views WHERE day < :cutoff"),
        {"cutoff": today() - timedelta(days=KEEP_DAYS)},
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# --- the numbers ---------------------------------------------------------------------------------

METRICS = ("views", "saves", "questions", "conversations", "orders", "units", "revenue_cents")


# Per listing and day, from the source tables. Orders count once per listing they include.
PER_DAY: dict[str, str] = {
    "views": """
        SELECT v.listing_id, v.day, v.views AS n FROM listing_daily_views v
        JOIN listings l ON l.id = v.listing_id
        WHERE l.seller_id = :seller AND v.day BETWEEN :start AND :end""",
    "saves": """
        SELECT s.listing_id, ((s.created_at) AT TIME ZONE 'America/Sao_Paulo')::date AS day,
               count(*) AS n
        FROM saves s
        JOIN listings l ON l.id = s.listing_id
        WHERE l.seller_id = :seller AND ((s.created_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end
        GROUP BY 1, 2""",
    "questions": """
        SELECT q.listing_id, ((q.created_at) AT TIME ZONE 'America/Sao_Paulo')::date AS day,
               count(*) AS n
        FROM questions q
        JOIN listings l ON l.id = q.listing_id
        WHERE l.seller_id = :seller AND ((q.created_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end
        GROUP BY 1, 2""",
    "conversations": """
        SELECT c.listing_id, ((c.created_at) AT TIME ZONE 'America/Sao_Paulo')::date AS day,
               count(*) AS n
        FROM conversations c
        WHERE c.seller_id = :seller AND ((c.created_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end
        GROUP BY 1, 2""",
}
SALES_PER_DAY = """
    SELECT i.listing_id, ((o.paid_at) AT TIME ZONE 'America/Sao_Paulo')::date AS day,
           count(DISTINCT o.id) AS orders,
           sum(i.quantity) AS units, sum(i.unit_price_cents * i.quantity) AS revenue
    FROM order_items i JOIN orders o ON o.id = i.order_id
    WHERE o.seller_id = :seller AND o.status IN ('paid', 'shipped', 'delivered')
      AND ((o.paid_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end
    GROUP BY 1, 2"""
# Shop totals by order (one order may hold several listings), and the coupons in them.
ORDER_TOTALS = """
    SELECT count(*) AS orders,
           coalesce(sum(o.items_cents - o.discount_cents), 0) AS revenue,
           count(*) FILTER (WHERE o.coupon_id IS NOT NULL) AS with_coupon,
           coalesce(sum(o.discount_cents), 0) AS discount,
           coalesce(sum(o.items_cents - o.discount_cents) FILTER (WHERE o.coupon_id IS NOT NULL), 0)
               AS revenue_with_coupon
    FROM orders o
    WHERE o.seller_id = :seller AND o.status IN ('paid', 'shipped', 'delivered')
      AND ((o.paid_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end"""
ORDERS_PER_DAY = """
    SELECT ((o.paid_at) AT TIME ZONE 'America/Sao_Paulo')::date AS day, count(*) AS orders,
           sum(o.items_cents - o.discount_cents) AS revenue
    FROM orders o
    WHERE o.seller_id = :seller AND o.status IN ('paid', 'shipped', 'delivered')
      AND ((o.paid_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end
    GROUP BY 1"""
REFUNDS = """
    SELECT count(*) FROM orders o
    WHERE o.seller_id = :seller AND o.status = 'refunded'
      AND ((o.paid_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end"""
BY_COUPON = """
    SELECT c.code, count(*) AS orders, sum(o.discount_cents) AS discount,
           sum(o.items_cents - o.discount_cents) AS revenue
    FROM orders o JOIN coupons c ON c.id = o.coupon_id
    WHERE o.seller_id = :seller AND o.status IN ('paid', 'shipped', 'delivered')
      AND ((o.paid_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end
    GROUP BY c.code ORDER BY count(*) DESC LIMIT 10"""
# Minutes from a buyer's first message to the seller's first answer after it.
RESPONSE = """
    WITH asked AS (
        SELECT c.id, c.seller_id,
               (SELECT min(m.created_at) FROM messages m
                 WHERE m.conversation_id = c.id AND m.sender_id = c.buyer_id) AS asked_at
        FROM conversations c
        WHERE c.seller_id = :seller AND ((c.created_at) AT TIME ZONE 'America/Sao_Paulo')::date
            BETWEEN :start AND :end
    )
    SELECT a.asked_at,
           (SELECT min(m.created_at) FROM messages m
             WHERE m.conversation_id = a.id AND m.sender_id = a.seller_id
               AND m.created_at > a.asked_at) AS answered_at
    FROM asked a WHERE a.asked_at IS NOT NULL"""


class DayPoint(BaseModel):
    date: date
    views: int
    saves: int
    questions: int
    conversations: int
    orders: int
    revenue_cents: int


class ListingRow(BaseModel):
    listing_id: uuid.UUID
    title: str
    status: str
    views: int
    saves: int
    questions: int
    conversations: int
    orders: int
    units: int
    revenue_cents: int
    # Orders per view, when the listing was seen at all.
    conversion: float | None


class Totals(BaseModel):
    views: int
    saves: int
    questions: int
    conversations: int
    orders: int
    revenue_cents: int
    refunds: int
    conversion: float | None


class ResponseTime(BaseModel):
    conversations: int
    answered: int
    median_minutes: float | None


class CouponRow(BaseModel):
    code: str
    orders: int
    discount_cents: int
    revenue_cents: int


class Coupons(BaseModel):
    orders_with_coupon: int
    discount_cents: int
    revenue_with_coupon_cents: int
    revenue_without_coupon_cents: int
    by_code: list[CouponRow]


class Analytics(BaseModel):
    start: date
    end: date
    totals: Totals
    series: list[DayPoint]
    listings: list[ListingRow]
    response: ResponseTime
    coupons: Coupons


async def _per_listing_day(
    db: AsyncSession, params: dict[str, Any]
) -> dict[tuple[uuid.UUID, date], dict[str, int]]:
    cells: dict[tuple[uuid.UUID, date], dict[str, int]] = defaultdict(
        lambda: dict.fromkeys(METRICS, 0)
    )
    for metric, sql in PER_DAY.items():
        for listing_id, day, n in await db.execute(text(sql), params):
            cells[(listing_id, day)][metric] += int(n)
    for listing_id, day, orders, units, revenue in await db.execute(text(SALES_PER_DAY), params):
        cell = cells[(listing_id, day)]
        cell["orders"] += int(orders)
        cell["units"] += int(units)
        cell["revenue_cents"] += int(revenue)
    return cells


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


async def seller_analytics(db: AsyncSession, seller_id: uuid.UUID, days: int) -> Analytics:
    end = today()
    start = end - timedelta(days=days - 1)
    params: dict[str, Any] = {"seller": seller_id, "start": start, "end": end}
    cells = await _per_listing_day(db, params)

    # Series: every day of the period, with orders and revenue counted once per order.
    daily: dict[date, dict[str, int]] = {
        start + timedelta(days=i): dict.fromkeys(METRICS, 0) for i in range(days)
    }
    for (_, day), cell in cells.items():
        for metric in ("views", "saves", "questions", "conversations"):
            daily[day][metric] += cell[metric]
    for day, orders, revenue in await db.execute(text(ORDERS_PER_DAY), params):
        daily[day]["orders"] = int(orders)
        daily[day]["revenue_cents"] = int(revenue)

    listings = {
        row.id: row
        for row in await db.execute(
            text(
                "SELECT id, title, status FROM listings"
                " WHERE seller_id = :seller AND status <> 'removed'"
            ),
            params,
        )
    }
    per_listing: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: dict.fromkeys(METRICS, 0))
    for (listing_id, _), cell in cells.items():
        for metric, n in cell.items():
            per_listing[listing_id][metric] += n
    titles = (
        dict(
            (row.id, (row.title, row.status))
            for row in await db.execute(
                text("SELECT id, title, status FROM listings WHERE id = ANY(:ids)"),
                {"ids": list(per_listing)},
            )
        )
        if per_listing
        else {}
    )
    rows = []
    for listing_id in set(listings) | set(per_listing):
        title, status = titles.get(listing_id) or (
            listings[listing_id].title,
            listings[listing_id].status,
        )
        m = per_listing[listing_id]
        rows.append(
            ListingRow(
                listing_id=listing_id,
                title=title,
                status=str(status),
                **{k: m[k] for k in METRICS},
                conversion=round(m["orders"] / m["views"], 4) if m["views"] else None,
            )
        )
    rows.sort(key=lambda r: (r.revenue_cents, r.orders, r.views), reverse=True)

    totals_row = (await db.execute(text(ORDER_TOTALS), params)).one()
    refunds = int(await db.scalar(text(REFUNDS), params) or 0)
    total_views = sum(d["views"] for d in daily.values())
    totals = Totals(
        views=total_views,
        saves=sum(d["saves"] for d in daily.values()),
        questions=sum(d["questions"] for d in daily.values()),
        conversations=sum(d["conversations"] for d in daily.values()),
        orders=int(totals_row.orders),
        revenue_cents=int(totals_row.revenue),
        refunds=refunds,
        conversion=round(int(totals_row.orders) / total_views, 4) if total_views else None,
    )

    conversations = (await db.execute(text(RESPONSE), params)).all()
    waits = [
        (answered - asked).total_seconds() / 60
        for asked, answered in conversations
        if answered is not None
    ]
    coupons = Coupons(
        orders_with_coupon=int(totals_row.with_coupon),
        discount_cents=int(totals_row.discount),
        revenue_with_coupon_cents=int(totals_row.revenue_with_coupon),
        revenue_without_coupon_cents=int(totals_row.revenue) - int(totals_row.revenue_with_coupon),
        by_code=[
            CouponRow(code=code, orders=int(n), discount_cents=int(d), revenue_cents=int(r))
            for code, n, d, r in await db.execute(text(BY_COUPON), params)
        ],
    )
    return Analytics(
        start=start,
        end=end,
        totals=totals,
        series=[
            DayPoint(date=day, **{k: v for k, v in m.items() if k != "units"})
            for day, m in sorted(daily.items())
        ],
        listings=rows,
        response=ResponseTime(
            conversations=len(conversations),
            answered=len(waits),
            median_minutes=round(median, 1) if (median := _median(waits)) is not None else None,
        ),
        coupons=coupons,
    )


async def seller_csv(db: AsyncSession, seller_id: uuid.UUID, days: int) -> str:
    end = today()
    start = end - timedelta(days=days - 1)
    params: dict[str, Any] = {"seller": seller_id, "start": start, "end": end}
    cells = await _per_listing_day(db, params)
    titles = {
        row.id: row.title
        for row in await db.execute(
            text("SELECT id, title FROM listings WHERE seller_id = :seller"), params
        )
    }
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date", "listing_id", "title", *METRICS[:-1], "revenue_brl"])
    for (listing_id, day), m in sorted(
        cells.items(), key=lambda kv: (kv[0][1], titles.get(kv[0][0], ""))
    ):
        writer.writerow(
            [
                day.isoformat(),
                listing_id,
                titles.get(listing_id, ""),
                *(m[k] for k in METRICS[:-1]),
                f"{m['revenue_cents'] / 100:.2f}",
            ]
        )
    return out.getvalue()


router = APIRouter(prefix="/api/me/analytics", tags=["analytics"])
DaysParam = Annotated[int, Query(ge=1, le=366)]


@router.get("")
async def my_analytics(user: CurrentUser, db: DbSession, days: DaysParam = 30) -> Analytics:
    return await seller_analytics(db, user.id, days)


@router.get("/export.csv")
async def my_analytics_csv(user: CurrentUser, db: DbSession, days: DaysParam = 30) -> Response:
    body = await seller_csv(db, user.id, days)
    name = f"tradepost-{today().isoformat()}-{days}d.csv"
    return Response(
        content="﻿" + body,  # the BOM makes spreadsheet apps read the accents right
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
