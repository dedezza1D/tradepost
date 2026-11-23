"""Password reset by e-mail (OWASP Forgot Password cheat sheet).

- The request answers the same way whether or not the address has an account, and the lookup,
  the token and the e-mail happen after the response is sent, so neither the answer nor its
  timing tells which addresses are registered.
- The link carries a random 256-bit token in the URL fragment, which browsers do not send to
  servers or in ``Referer``. Only its SHA-256 is stored. A link works once and for
  ``RESET_TTL``; asking for a new one, using one or changing the password deletes the others.
- Demonstration accounts and accounts without a usable password get no link.
- Setting the new password follows the sign-up rules, signs every session out and tells the
  owner by e-mail. The browser that reset it becomes a trusted device for sign-in.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.db import Database
from tradepost.errors import AppError
from tradepost.identity.models import PasswordResetToken, User
from tradepost.logs import error_summary, get_logger
from tradepost.notifications.email import EmailSender, compose_account
from tradepost.security.passwords import has_usable_password

log = get_logger(__name__)

RESET_TTL = timedelta(minutes=30)
RESET_PATH = "/reset-password"
FORGOT_PATH = "/forgot-password"
# Addresses on these domains are seeded demonstration accounts: nobody reads their mail.
NO_RESET_DOMAINS = frozenset({"demo.tradepost.invalid"})


class InvalidResetLink(AppError):
    status_code = 400
    code = "reset_link_invalid"


class EmailUnavailable(AppError):
    status_code = 503
    code = "email_unavailable"


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def can_reset(user: User) -> bool:
    domain = user.email.rsplit("@", 1)[-1]
    return domain not in NO_RESET_DOMAINS and has_usable_password(user.password_hash)


async def issue_token(db: AsyncSession, user_id: uuid.UUID) -> str:
    """A new reset token for the user; the earlier ones stop working."""
    await forget_tokens(db, user_id)
    token = secrets.token_urlsafe(32)
    db.add(
        PasswordResetToken(
            user_id=user_id,
            token_hash=_hash_token(token),
            expires_at=datetime.now(UTC) + RESET_TTL,
        )
    )
    await db.flush()
    return token


async def forget_tokens(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))


async def user_for_token(db: AsyncSession, token: str) -> tuple[PasswordResetToken, User]:
    """The token's row and account, or ``InvalidResetLink`` when the link is unknown, used,
    replaced or expired."""
    row = (
        await db.execute(
            select(PasswordResetToken, User)
            .join(User, User.id == PasswordResetToken.user_id)
            .where(
                PasswordResetToken.token_hash == _hash_token(token),
                PasswordResetToken.expires_at > datetime.now(UTC),
            )
        )
    ).one_or_none()
    if row is None or not can_reset(row[1]):
        raise InvalidResetLink("This link is invalid or has expired.")
    return row[0], row[1]


async def consume_token(db: AsyncSession, token_id: uuid.UUID) -> None:
    """Delete the token; of two requests racing on one link, only the first gets past this."""
    deleted = await db.execute(
        delete(PasswordResetToken)
        .where(PasswordResetToken.id == token_id)
        .returning(PasswordResetToken.id)
    )
    if deleted.scalar_one_or_none() is None:
        raise InvalidResetLink("This link is invalid or has expired.")


async def send_reset_link(database: Database, sender: EmailSender, origin: str, email: str) -> None:
    """Background task after a reset request: e-mail a link when the account may have one."""
    try:
        async with database.sessionmaker() as db, db.begin():
            user = await db.scalar(select(User).where(User.email == email))
            if user is None or not can_reset(user):
                return
            token = await issue_token(db, user.id)
            name, locale = user.display_name, user.locale
        link = f"{origin.rstrip('/')}{RESET_PATH}#token={token}"
        minutes = int(RESET_TTL.total_seconds() // 60)
        await sender.send(
            compose_account(
                to=email,
                name=name,
                kind="reset",
                link=link,
                locale=locale,
                values={"minutes": minutes},
            )
        )
    except Exception as exc:  # A background task has nobody to answer to: log and move on.
        log.warning("password_reset_email_failed", error=error_summary(exc))


async def send_password_changed(
    sender: EmailSender | None, origin: str, *, to: str, name: str, locale: str
) -> None:
    """Background task after a password change: tell the owner, with a way back in."""
    if sender is None or not sender.enabled:
        return
    try:
        await sender.send(
            compose_account(
                to=to,
                name=name,
                kind="changed",
                link=origin.rstrip("/") + FORGOT_PATH,
                locale=locale,
            )
        )
    except Exception as exc:
        log.warning("password_changed_email_failed", error=error_summary(exc))


async def purge_expired(db: AsyncSession) -> int:
    """Job: delete links past their deadline."""
    result = await db.execute(
        delete(PasswordResetToken).where(PasswordResetToken.expires_at < datetime.now(UTC))
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
