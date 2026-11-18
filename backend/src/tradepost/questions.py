"""Public questions on a listing. Anyone signed in asks; only the seller answers.

Questions and answers are public, so text with contact details or payment outside the platform
is refused here (in the private chat it is only flagged).
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import DateTime, ForeignKey, Index, String, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.catalog.models import Listing, ListingStatus
from tradepost.chat.detection import warning_for
from tradepost.db import Base, new_id
from tradepost.deps import DbSession
from tradepost.errors import AppError, Forbidden, NotFound
from tradepost.identity.models import User
from tradepost.identity.sessions import CurrentUser, OptionalPrincipal
from tradepost.notifications.service import notify
from tradepost.security.ratelimit import rate_limit
from tradepost.social.blocks import is_blocked


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (Index("ix_questions_listing", "listing_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), nullable=False
    )
    asker_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    answer: Mapped[str | None] = mapped_column(String(1000))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


QuestionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)
]
AnswerText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class QuestionIn(BaseModel):
    body: QuestionText


class AnswerIn(BaseModel):
    body: AnswerText


class QuestionOut(BaseModel):
    id: uuid.UUID
    body: str
    asker_name: str
    mine: bool
    answer: str | None
    answered_at: datetime | None
    created_at: datetime


class QuestionPage(BaseModel):
    items: list[QuestionOut]
    unanswered: int
    next_before: datetime | None


def _public_text(text: str) -> None:
    if warning_for(text):
        raise AppError(
            "Public questions and answers cannot carry contact details or payment instructions. "
            "Use the private chat.",
            code="contact_not_allowed",
        )


async def _listing(db: AsyncSession, listing_id: uuid.UUID) -> Listing:
    listing = await db.get(Listing, listing_id)
    if listing is None or listing.status in (ListingStatus.DRAFT, ListingStatus.REMOVED):
        raise NotFound("Listing not found.")
    return listing


router = APIRouter(prefix="/api", tags=["questions"])


@router.get("/listings/{listing_id}/questions")
async def list_questions(
    listing_id: uuid.UUID,
    principal: OptionalPrincipal,
    db: DbSession,
    before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> QuestionPage:
    """Answered questions for everyone; unanswered ones only for the seller and the asker."""
    listing = await _listing(db, listing_id)
    viewer = principal.user if principal else None
    is_seller = viewer is not None and viewer.id == listing.seller_id
    visible = [Question.listing_id == listing.id, Question.hidden_at.is_(None)]
    if not is_seller:
        visible.append(
            or_(Question.answer.is_not(None), Question.asker_id == (viewer.id if viewer else None))
        )
    stmt = select(Question, User.display_name).join(User, User.id == Question.asker_id)
    stmt = stmt.where(*visible)
    if before is not None:
        stmt = stmt.where(Question.created_at < before)
    rows = (await db.execute(stmt.order_by(Question.created_at.desc()).limit(limit))).all()
    unanswered = 0
    if is_seller:
        unanswered = int(
            (
                await db.execute(
                    select(func.count()).where(
                        Question.listing_id == listing.id,
                        Question.hidden_at.is_(None),
                        Question.answer.is_(None),
                    )
                )
            ).scalar_one()
        )
    return QuestionPage(
        items=[
            QuestionOut(
                id=q.id,
                body=q.body,
                # First name only: questions are public.
                asker_name=name.split()[0],
                mine=viewer is not None and q.asker_id == viewer.id,
                answer=q.answer,
                answered_at=q.answered_at,
                created_at=q.created_at,
            )
            for q, name in rows
        ],
        unanswered=unanswered,
        next_before=rows[-1][0].created_at if len(rows) == limit else None,
    )


@router.post(
    "/listings/{listing_id}/questions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("question", limit=20, window_seconds=3600))],
)
async def ask(
    listing_id: uuid.UUID, body: QuestionIn, user: CurrentUser, db: DbSession
) -> QuestionOut:
    listing = await _listing(db, listing_id)
    if listing.seller_id == user.id:
        raise AppError(
            "Sellers answer questions; they do not ask them.", code="own_listing_question"
        )
    if await is_blocked(db, user.id, listing.seller_id):
        raise NotFound("Listing not found.")
    _public_text(body.body)
    question = Question(listing_id=listing.id, asker_id=user.id, body=body.body)
    db.add(question)
    await db.flush()
    await db.refresh(question)
    await notify(
        db,
        listing.seller_id,
        "question_received",
        {"listing_id": str(listing.id), "title": listing.title, "name": user.display_name},
    )
    return QuestionOut(
        id=question.id,
        body=question.body,
        asker_name=user.display_name.split()[0],
        mine=True,
        answer=None,
        answered_at=None,
        created_at=question.created_at,
    )


async def _question(db: AsyncSession, question_id: uuid.UUID) -> tuple[Question, Listing]:
    question = await db.get(Question, question_id)
    if question is None or question.hidden_at is not None:
        raise NotFound("Question not found.")
    listing = await db.get(Listing, question.listing_id)
    assert listing is not None
    return question, listing


@router.post(
    "/questions/{question_id}/answer",
    dependencies=[Depends(rate_limit("answer", limit=60, window_seconds=3600))],
)
async def answer(
    question_id: uuid.UUID, body: AnswerIn, user: CurrentUser, db: DbSession
) -> QuestionOut:
    question, listing = await _question(db, question_id)
    if listing.seller_id != user.id:
        raise Forbidden("Only the seller answers questions.", code="seller_only")
    _public_text(body.body)
    first_answer = question.answer is None
    question.answer = body.body
    question.answered_at = datetime.now(UTC)
    asker = await db.get(User, question.asker_id)
    if first_answer:
        await notify(
            db,
            question.asker_id,
            "question_answered",
            {"listing_id": str(listing.id), "title": listing.title},
        )
    return QuestionOut(
        id=question.id,
        body=question.body,
        asker_name=asker.display_name.split()[0] if asker else "",
        mine=False,
        answer=question.answer,
        answered_at=question.answered_at,
        created_at=question.created_at,
    )


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def hide(question_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """The seller and admins can hide any question; the asker can withdraw an unanswered one."""
    question, listing = await _question(db, question_id)
    allowed = (
        user.is_admin
        or user.id == listing.seller_id
        or (user.id == question.asker_id and question.answer is None)
    )
    if not allowed:
        raise Forbidden("You cannot remove this question.")
    question.hidden_at = datetime.now(UTC)
