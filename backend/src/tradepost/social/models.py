"""Social graph and safety: follows, saved listings, blocks and reports."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base, new_id


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (
        CheckConstraint("follower_id <> followee_id", name="not_self"),
        Index("ix_follows_followee", "followee_id", "created_at"),
        Index("ix_follows_follower_created", "follower_id", "created_at"),
    )

    follower_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    followee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = _created_at()


class Save(Base):
    """A listing a user saved. The prices remember what the user already knows, so a price
    drop is announced once per new low and never for the price they saved at."""

    __tablename__ = "saves"
    __table_args__ = (
        Index("ix_saves_listing", "listing_id"),
        Index("ix_saves_user_created", "user_id", "created_at"),
        Index("ix_saves_created", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listings.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = _created_at()
    price_cents_at_save: Mapped[int] = mapped_column(BigInteger, nullable=False)
    alerted_price_cents: Mapped[int | None] = mapped_column(BigInteger)
    low_stock_alerted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class Block(Base):
    __tablename__ = "blocks"
    __table_args__ = (
        CheckConstraint("blocker_id <> blocked_id", name="not_self"),
        Index("ix_blocks_blocked", "blocked_id"),
    )

    blocker_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    blocked_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = _created_at()


class ReportTarget(enum.StrEnum):
    LISTING = "listing"
    USER = "user"


class ReportReason(enum.StrEnum):
    PROHIBITED = "prohibited"
    SCAM = "scam"
    OFFENSIVE = "offensive"
    SPAM = "spam"
    OTHER = "other"


class ReportStatus(enum.StrEnum):
    OPEN = "open"
    DISMISSED = "dismissed"
    ACTIONED = "actioned"


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        # One open report per person and target: reporting twice does not add weight.
        Index(
            "uq_reports_open_per_reporter",
            "reporter_id",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_reports_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[ReportTarget] = mapped_column(
        _enum(ReportTarget, "report_target"), nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reason: Mapped[ReportReason] = mapped_column(
        _enum(ReportReason, "report_reason"), nullable=False
    )
    details: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[ReportStatus] = mapped_column(
        _enum(ReportStatus, "report_status"), nullable=False, default=ReportStatus.OPEN
    )
    created_at: Mapped[datetime] = _created_at()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Suggested by the triage job for the admin queue (1 urgent, 2 soon, 3 low).
    triage_category: Mapped[str | None] = mapped_column(String(20))
    triage_priority: Mapped[int | None] = mapped_column(SmallInteger)
    triage_reason: Mapped[str | None] = mapped_column(String(300))
    triaged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
