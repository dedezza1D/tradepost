"""Seller coupons: a percentage or a fixed amount off that seller's items.

A use is reserved when a checkout opens (a conditional UPDATE under the limit), so concurrent
buyers cannot exceed it, and given back if the checkout expires or is cancelled. Each buyer can
use a code once. Shipping is never discounted, and the platform fee is charged on the
discounted price.
"""

import enum
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, StringConstraints, model_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base, new_id
from tradepost.deps import DbSession
from tradepost.errors import AppError, Conflict, NotFound
from tradepost.identity.sessions import CurrentUser
from tradepost.security.ratelimit import rate_limit

MAX_COUPONS_PER_SELLER = 50
# Payment providers refuse tiny charges: a discount never takes a seller's part below this.
MIN_CHARGE_CENTS = 100


class CouponKind(enum.StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"


class Coupon(Base):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("seller_id", "code"),
        CheckConstraint("value > 0", name="value_positive"),
        CheckConstraint("max_uses IS NULL OR uses <= max_uses", name="uses_within_limit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    seller_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[CouponKind] = mapped_column(
        Enum(CouponKind, name="coupon_kind", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    # Percent (1-90) or cents.
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    min_items_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def normalize(code: str) -> str:
    return "".join(code.split()).upper()


def discount_for(coupon: Coupon, items_cents: int, shipping_cents: int) -> int:
    if coupon.kind == CouponKind.PERCENT:
        amount = items_cents * coupon.value // 100
    else:
        amount = min(coupon.value, items_cents)
    return max(0, min(amount, items_cents + shipping_cents - MIN_CHARGE_CENTS))


async def find_usable(
    db: AsyncSession, code: str, seller_ids: set[uuid.UUID], buyer_id: uuid.UUID
) -> Coupon:
    """The coupon with this code from one of the sellers in the checkout, if the buyer can use
    it now. Limits are checked again, atomically, when the use is reserved."""
    coupon = (
        await db.execute(
            select(Coupon).where(Coupon.code == normalize(code), Coupon.seller_id.in_(seller_ids))
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if (
        coupon is None
        or not coupon.active
        or (coupon.expires_at is not None and coupon.expires_at <= now)
        or (coupon.max_uses is not None and coupon.uses >= coupon.max_uses)
    ):
        raise AppError("This coupon is not valid for these items.", code="coupon_invalid")
    used = (
        await db.execute(
            text(
                "SELECT 1 FROM orders WHERE coupon_id = :c AND buyer_id = :b"
                " AND status <> 'cancelled' LIMIT 1"
            ),
            {"c": coupon.id, "b": buyer_id},
        )
    ).first()
    if used is not None:
        raise AppError("You already used this coupon.", code="coupon_used")
    return coupon


async def reserve_use(db: AsyncSession, coupon_id: uuid.UUID) -> None:
    taken = (
        await db.execute(
            text(
                "UPDATE coupons SET uses = uses + 1 WHERE id = :id AND active"
                " AND (max_uses IS NULL OR uses < max_uses)"
                " AND (expires_at IS NULL OR expires_at > now()) RETURNING id"
            ),
            {"id": coupon_id},
        )
    ).first()
    if taken is None:
        raise Conflict("This coupon was just used up.", code="coupon_invalid")


async def release_use(db: AsyncSession, coupon_id: uuid.UUID) -> None:
    await db.execute(
        text("UPDATE coupons SET uses = uses - 1 WHERE id = :id AND uses > 0"), {"id": coupon_id}
    )


# --- seller API -------------------------------------------------------------------------------

Code = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=20, pattern=r"^[A-Za-z0-9_-]+$"
    ),
]


class CouponIn(BaseModel):
    code: Code
    kind: CouponKind
    value: int = Field(gt=0, le=10_000_000)
    min_items_cents: int = Field(default=0, ge=0, le=100_000_000)
    max_uses: int | None = Field(default=None, ge=1, le=100_000)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _percent_range(self) -> "CouponIn":
        if self.kind == CouponKind.PERCENT and self.value > 90:
            raise ValueError("a percentage coupon is at most 90")
        return self


class CouponPatch(BaseModel):
    active: bool


class CouponOut(BaseModel):
    id: uuid.UUID
    code: str
    kind: CouponKind
    value: int
    min_items_cents: int
    max_uses: int | None
    uses: int
    expires_at: datetime | None
    active: bool
    created_at: datetime


router = APIRouter(prefix="/api/me/coupons", tags=["coupons"])


def _out(c: Coupon) -> CouponOut:
    return CouponOut.model_validate(c, from_attributes=True)


@router.get("")
async def list_coupons(user: CurrentUser, db: DbSession) -> list[CouponOut]:
    rows = await db.execute(
        select(Coupon).where(Coupon.seller_id == user.id).order_by(Coupon.created_at.desc())
    )
    return [_out(c) for c in rows.scalars()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit("coupon", limit=30, window_seconds=3600))],
)
async def create_coupon(body: CouponIn, user: CurrentUser, db: DbSession) -> CouponOut:
    count = (await db.execute(select(func.count()).where(Coupon.seller_id == user.id))).scalar_one()
    if count >= MAX_COUPONS_PER_SELLER:
        raise AppError("Coupon limit reached.", code="coupon_limit")
    coupon = Coupon(seller_id=user.id, **{**body.model_dump(), "code": normalize(body.code)})
    try:
        async with db.begin_nested():
            db.add(coupon)
    except IntegrityError:
        raise Conflict("You already have a coupon with this code.", code="coupon_exists") from None
    await db.refresh(coupon)
    return _out(coupon)


@router.patch("/{coupon_id}")
async def update_coupon(
    coupon_id: uuid.UUID, body: CouponPatch, user: CurrentUser, db: DbSession
) -> CouponOut:
    coupon = await db.get(Coupon, coupon_id)
    if coupon is None or coupon.seller_id != user.id:
        raise NotFound("Coupon not found.")
    coupon.active = body.active
    await db.flush()
    await db.refresh(coupon)
    return _out(coupon)
