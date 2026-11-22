import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.config import Settings, get_settings
from tradepost.deps import DbSession
from tradepost.errors import Forbidden, Unauthorized
from tradepost.identity.models import Session, User

SESSION_COOKIE = "tp_session"
CSRF_COOKIE = "tp_csrf"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_TOUCH_INTERVAL = timedelta(minutes=5)


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True)
class Principal:
    user: User
    session: Session


async def create_session(
    db: AsyncSession, response: Response, user: User, *, user_agent: str | None
) -> Session:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    session = Session(
        user_id=user.id,
        token_hash=_hash_token(token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours),
        user_agent=(user_agent or "")[:300] or None,
    )
    db.add(session)
    await db.flush()
    _set_cookies(response, settings, token, session.csrf_token)
    return session


def _set_cookies(response: Response, settings: Settings, token: str, csrf: str) -> None:
    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Readable by the web app, which echoes it in the CSRF header on unsafe requests.
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


async def revoke_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    await db.execute(
        update(Session)
        .where(Session.id == session_id, Session.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def revoke_all_sessions(
    db: AsyncSession, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None
) -> None:
    stmt = update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
    if except_id is not None:
        stmt = stmt.where(Session.id != except_id)
    await db.execute(stmt.values(revoked_at=datetime.now(UTC)))


# Sessions that ended (expired or signed out) are kept this long, then deleted: they hold a
# device description and dates of use, and no longer serve any purpose.
SESSION_RETENTION = timedelta(days=30)


async def purge_sessions(db: AsyncSession, *, retention: timedelta = SESSION_RETENTION) -> int:
    cutoff = datetime.now(UTC) - retention
    result = await db.execute(
        delete(Session).where(or_(Session.expires_at < cutoff, Session.revoked_at < cutoff))
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def load_principal(request: Request, db: AsyncSession) -> Principal | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or len(token) > 100:
        return None
    now = datetime.now(UTC)
    row = (
        await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(
                Session.token_hash == _hash_token(token),
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    session, user = row
    if now - session.last_seen_at > _TOUCH_INTERVAL:
        session.last_seen_at = now
    return Principal(user=user, session=session)


def _check_csrf(request: Request, session: Session) -> None:
    if request.method in _SAFE_METHODS:
        return
    sent = request.headers.get(CSRF_HEADER, "")
    if not hmac.compare_digest(sent.encode(), session.csrf_token.encode()):
        raise Forbidden("Missing or invalid CSRF token.", code="csrf_failed")


async def optional_principal(request: Request, db: DbSession) -> Principal | None:
    principal = await load_principal(request, db)
    if principal is not None:
        _check_csrf(request, principal.session)
    return principal


async def require_principal(
    principal: Annotated[Principal | None, Depends(optional_principal)],
) -> Principal:
    if principal is None:
        raise Unauthorized("Sign in to continue.")
    return principal


async def require_user(principal: Annotated[Principal, Depends(require_principal)]) -> User:
    return principal.user


async def require_admin(user: Annotated[User, Depends(require_user)]) -> User:
    if not user.is_admin:
        raise Forbidden("Administrators only.")
    return user


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
CurrentUser = Annotated[User, Depends(require_user)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]
AdminUser = Annotated[User, Depends(require_admin)]
