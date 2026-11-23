import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base, TimestampMixin, new_id


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    # Stored lower-cased; uniqueness is enforced on this column.
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(60), nullable=False)
    city: Mapped[str | None] = mapped_column(String(80))
    state: Mapped[str | None] = mapped_column(String(2))
    municipality_id: Mapped[int | None] = mapped_column(
        ForeignKey("municipalities.ibge_code", ondelete="SET NULL")
    )
    locale: Mapped[str] = mapped_column(String(5), nullable=False, server_default="pt-BR")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bio: Mapped[str | None] = mapped_column(String(300))
    avatar_key: Mapped[str | None] = mapped_column(String(200))
    # Stored size of the avatar, counted against the media quota.
    avatar_bytes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Kept in step with ``follows`` in the same transaction as each follow or unfollow.
    follower_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    following_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Session(TimestampMixin, Base):
    """Server-side session. The cookie carries a random token; only its SHA-256 is stored."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id_active", "user_id", "revoked_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    # 0: password verified, second factor still owed; 1: password; 2: password and second
    # factor (see identity/sessions.py).
    auth_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, server_default="1"
    )


class PasswordResetToken(Base):
    """A password reset link. The link carries a random token; only its SHA-256 is stored.
    The row is deleted when the link is used, when a newer one is asked for and when the
    password changes; expired rows are purged by a job."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TotpFactor(TimestampMixin, Base):
    """An authenticator app (TOTP) registered for an account. The shared secret is stored
    encrypted (identity/mfa.py); the factor counts only once confirmed with a valid code."""

    __tablename__ = "totp_factors"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The time step of the last accepted code: a code is never accepted twice.
    last_used_step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class RecoveryCode(Base):
    """A single-use recovery code for an account with a second factor. Only its SHA-256 is
    stored (the codes are random, so a fast hash is enough)."""

    __tablename__ = "recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
