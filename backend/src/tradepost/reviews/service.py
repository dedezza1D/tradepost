"""Buyer reviews of sellers. One review per order, only after the buyer has the item."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base, new_id
from tradepost.deps import DbSession
from tradepost.errors import AppError, Conflict, Forbidden, NotFound
from tradepost.identity.models import User
from tradepost.identity.sessions import CurrentUser
from tradepost.notifications.service import notify
from tradepost.orders.models import Order, OrderStatus


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (CheckConstraint("rating BETWEEN 1 AND 5", name="rating_range"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), unique=True, nullable=False
    )
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: Comment | None = None


class ReviewOut(BaseModel):
    id: uuid.UUID
    rating: int
    comment: str | None
    buyer_name: str
    items: list[str]
    created_at: datetime


class SellerReviewsOut(BaseModel):
    seller_id: uuid.UUID
    display_name: str
    city: str | None
    state: str | None
    member_since: datetime
    rating_avg: float | None
    rating_count: int
    reviews: list[ReviewOut]


async def reviewed_order_ids(db: AsyncSession, order_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not order_ids:
        return set()
    rows = await db.execute(select(Review.order_id).where(Review.order_id.in_(order_ids)))
    return set(rows.scalars())


async def seller_rating(db: AsyncSession, seller_id: uuid.UUID) -> tuple[float | None, int]:
    avg, count = (
        await db.execute(
            select(func.avg(Review.rating), func.count(Review.id)).where(
                Review.seller_id == seller_id
            )
        )
    ).one()
    return (round(float(avg), 2) if avg is not None else None, int(count))


router = APIRouter(prefix="/api", tags=["reviews"])


@router.post("/orders/{order_id}/review", status_code=status.HTTP_201_CREATED)
async def create_review(
    order_id: uuid.UUID, body: ReviewIn, user: CurrentUser, db: DbSession
) -> ReviewOut:
    order = await db.get(Order, order_id)
    if order is None or user.id not in (order.buyer_id, order.seller_id):
        raise NotFound("Order not found.")
    if order.buyer_id != user.id:
        raise Forbidden("Only the buyer can review this order.")
    if order.status != OrderStatus.DELIVERED:
        raise AppError("You can review an order after it is delivered.", code="not_delivered")
    review = Review(
        order_id=order.id,
        buyer_id=user.id,
        seller_id=order.seller_id,
        rating=body.rating,
        comment=body.comment or None,
    )
    db.add(review)
    try:
        await db.flush()
    except IntegrityError:
        raise Conflict("This order was already reviewed.", code="already_reviewed") from None
    await db.refresh(review)
    await notify(
        db,
        order.seller_id,
        "review_received",
        {"order_id": str(order.id), "rating": review.rating, "title": order.items[0].title},
    )
    return ReviewOut(
        id=review.id,
        rating=review.rating,
        comment=review.comment,
        buyer_name=user.display_name,
        items=[i.title for i in order.items],
        created_at=review.created_at,
    )


@router.get("/users/{user_id}/reviews")
async def seller_reviews(user_id: uuid.UUID, db: DbSession) -> SellerReviewsOut:
    seller = await db.get(User, user_id)
    if seller is None:
        raise NotFound("User not found.")
    rows = (
        await db.execute(
            select(Review, User.display_name, Order)
            .join(User, User.id == Review.buyer_id)
            .join(Order, Order.id == Review.order_id)
            .where(Review.seller_id == user_id)
            .order_by(Review.created_at.desc())
            .limit(50)
        )
    ).all()
    avg, count = await seller_rating(db, user_id)
    return SellerReviewsOut(
        seller_id=seller.id,
        display_name=seller.display_name,
        city=seller.city,
        state=seller.state,
        member_since=seller.created_at,
        rating_avg=avg,
        rating_count=count,
        reviews=[
            ReviewOut(
                id=r.id,
                rating=r.rating,
                comment=r.comment,
                buyer_name=name,
                items=[i.title for i in order.items],
                created_at=r.created_at,
            )
            for r, name, order in rows
        ],
    )
