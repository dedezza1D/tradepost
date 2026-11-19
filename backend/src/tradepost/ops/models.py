"""Operational data kept in PostgreSQL: request metrics per minute, grouped errors, job runs,
alert state and feature switches. Small, bounded tables: old rows are purged by a job."""

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base


class RequestStat(Base):
    """Requests of one route in one minute. Latency is kept as a histogram (fixed buckets), so
    percentiles can be estimated after aggregation, which an average cannot give."""

    __tablename__ = "request_stats"

    minute: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    method: Mapped[str] = mapped_column(String(8), primary_key=True)
    route: Mapped[str] = mapped_column(String(160), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    errors_5xx: Mapped[int] = mapped_column(Integer, nullable=False)
    errors_4xx: Mapped[int] = mapped_column(Integer, nullable=False)
    total_ms: Mapped[float] = mapped_column(Float, nullable=False)
    buckets: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)


class ErrorEvent(Base):
    """Unhandled errors grouped by fingerprint (type, route and the deepest frame in our code)."""

    __tablename__ = "error_events"

    fingerprint: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(120), nullable=False)
    route: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(String(300), nullable=False)
    traceback: Mapped[str] = mapped_column(Text, nullable=False)
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_request_id: Mapped[str | None] = mapped_column(String(64))


class JobRun(Base):
    __tablename__ = "job_runs"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    last_started: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(300))
    consecutive_failures: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    last_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0)


class AlertState(Base):
    """Which alerts are firing, so admins are told once when one starts, not on every tick."""

    __tablename__ = "alert_state"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeatureFlag(Base):
    """Switches an admin can flip at runtime (the AI features, for instance)."""

    __tablename__ = "feature_flags"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
