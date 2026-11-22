"""Moderation in the background and for admins: the review queue, report triage, chat screening.

- Listings held at publishing wait in a queue; an admin approves (the listing goes live) or
  rejects it (removed, the seller is told).
- A job adds a suggested category, priority and reason to each new report, so the admin queue
  shows the urgent ones first. The admin's decision is recorded next to the suggestion.
- When a model is available, a job checks recent chat messages for scams the word rules miss
  and adds a warning next to them. It never hides or blocks a message.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.ai.client import AI
from tradepost.catalog.models import Listing, ListingStatus
from tradepost.chat.detection import CONTACT, OFF_PLATFORM_PAYMENT
from tradepost.chat.models import Conversation, Message, MessageKind
from tradepost.chat.service import wake
from tradepost.deps import DbSession
from tradepost.errors import AppError, NotFound
from tradepost.identity.sessions import AdminUser
from tradepost.moderation import classify_message, triage_report
from tradepost.notifications.service import notify
from tradepost.social.models import Report, ReportStatus, ReportTarget

BATCH = 20
SCREEN_WINDOW = timedelta(hours=24)


# --- jobs ----------------------------------------------------------------------------------------


async def _report_target_text(db: AsyncSession, report: Report) -> str:
    if report.target_type == ReportTarget.LISTING:
        listing = await db.get(Listing, report.target_id)
        return f"{listing.title}. {listing.description}" if listing else ""
    messages = await db.scalars(
        select(Message.body)
        .where(Message.sender_id == report.target_id, Message.kind == MessageKind.TEXT)
        .order_by(Message.id.desc())
        .limit(5)
    )
    return " | ".join(body for body in messages if body)


async def triage_reports(db: AsyncSession, ai: AI | None) -> int:
    """Job: suggest a category and priority for open reports not seen yet."""
    reports = (
        await db.scalars(
            select(Report)
            .where(Report.status == ReportStatus.OPEN, Report.triaged_at.is_(None))
            .order_by(Report.created_at)
            .limit(BATCH)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for report in reports:
        target = await _report_target_text(db, report)
        triage = await triage_report(db, ai, report.reason.value, report.details or "", target)
        report.triage_category = triage.category[:20]
        report.triage_priority = triage.priority
        report.triage_reason = triage.reason or None
        report.triaged_at = datetime.now(UTC)
    return len(reports)


async def screen_messages(db: AsyncSession, ai: AI | None) -> int:
    """Job: with a model, warn about scam messages the word rules let through."""
    if ai is None or not await ai.available(db):
        return 0
    since = datetime.now(UTC) - SCREEN_WINDOW
    rows = (
        await db.execute(
            select(Message, Conversation)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.kind == MessageKind.TEXT,
                Message.screened_at.is_(None),
                Message.created_at >= since,
            )
            .order_by(Message.id)
            .limit(BATCH)
            .with_for_update(of=Message, skip_locked=True)
        )
    ).all()
    for message, conversation in rows:
        verdict = await classify_message(db, ai, message.body or "")
        message.screened_at = datetime.now(UTC)
        if verdict.source != "model":
            continue
        before = message.warning
        if verdict.scam and before in (None, CONTACT):
            message.warning = verdict.kind if verdict.kind != "none" else "scam"
        elif not verdict.scam and before == OFF_PLATFORM_PAYMENT:
            # The word rule warned about "Pix" in an honest message (paying by Pix through the
            # checkout); the model reads it in context and the warning goes.
            message.warning = None
        if message.warning != before:
            # Both open chat screens refresh.
            await wake(db, conversation.buyer_id)
            await wake(db, conversation.seller_id)
    return len(rows)


# --- admin queue --------------------------------------------------------------------------------

router = APIRouter(prefix="/api/admin/review", tags=["moderation"])


class ReviewItem(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    seller_id: uuid.UUID
    category_slug: str
    price_cents: int
    review_category: str | None
    review_reason: str | None
    updated_at: datetime


class Decision(BaseModel):
    decision: Literal["approve", "reject"]


@router.get("/listings")
async def review_queue(admin: AdminUser, db: DbSession) -> list[ReviewItem]:
    listings = await db.scalars(
        select(Listing)
        .where(Listing.status == ListingStatus.IN_REVIEW)
        .order_by(Listing.updated_at)
        .limit(100)
    )
    return [
        ReviewItem(
            id=listing.id,
            title=listing.title,
            description=listing.description,
            seller_id=listing.seller_id,
            category_slug=listing.category_slug,
            price_cents=listing.price_cents,
            review_category=listing.review_category,
            review_reason=listing.review_reason,
            updated_at=listing.updated_at,
        )
        for listing in listings
    ]


@router.post("/listings/{listing_id}")
async def decide(
    listing_id: uuid.UUID,
    body: Decision,
    admin: AdminUser,
    db: DbSession,
) -> dict[str, str]:
    listing = await db.get(Listing, listing_id, with_for_update=True)
    if listing is None:
        raise NotFound("Listing not found.")
    if listing.status != ListingStatus.IN_REVIEW:
        raise AppError("This listing is not waiting for review.", code="not_in_review")
    payload = {"listing_id": str(listing.id), "title": listing.title}
    if body.decision == "approve":
        listing.status = ListingStatus.ACTIVE
        if listing.published_at is None:
            listing.published_at = datetime.now(UTC)
        await notify(db, listing.seller_id, "listing_approved", payload)
    else:
        listing.status = ListingStatus.REMOVED
        await notify(db, listing.seller_id, "listing_removed", payload)
    await db.flush()
    return {"status": listing.status.value}
