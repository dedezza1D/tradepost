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
# Over HTTPS the cookies carry the __Host- prefix: the browser then only accepts them when set
# Secure, for Path=/ and without Domain, so no other host or path can plant or shadow them.
# Plain-HTTP development cannot use the prefix and keeps the bare names.
HOST_PREFIX = "__Host-"
CSRF_HEADER = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# How a session was authenticated. A pending session proved the password and still owes the
# second factor: it is not a sign-in (no route accepts it except the one that takes the code)
# and lasts a few minutes.
AUTH_PENDING = 0
AUTH_PASSWORD = 1
AUTH_MFA = 2
PENDING_TTL = timedelta(minutes=5)
_TOUCH_INTERVAL = timedelta(minutes=5)


def cookie_names(settings: Settings) -> tuple[str, str]:
    """The session and CSRF cookie names in use."""
    prefix = HOST_PREFIX if settings.cookie_secure else ""
    return prefix + SESSION_COOKIE, prefix + CSRF_COOKIE


def session_limits(settings: Settings, user: User) -> tuple[timedelta, timedelta]:
    """Absolute and idle lifetimes of a session for this user."""
    if user.is_admin:
        return (
            timedelta(hours=settings.admin_session_ttl_hours),
            timedelta(minutes=settings.admin_session_idle_minutes),
        )
    return timedelta(hours=settings.session_ttl_hours), timedelta(hours=settings.session_idle_hours)


def _hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True)
class Principal:
    user: User
    session: Session


async def create_session(
    db: AsyncSession,
    response: Response,
    user: User,
    *,
    user_agent: str | None,
    auth_level: int = AUTH_PASSWORD,
) -> Session:
    settings = get_settings()
    lifetime = PENDING_TTL if auth_level == AUTH_PENDING else session_limits(settings, user)[0]
    token = secrets.token_urlsafe(32)
    session = Session(
        user_id=user.id,
        token_hash=_hash_token(token),
        csrf_token=secrets.token_urlsafe(32),
        expires_at=datetime.now(UTC) + lifetime,
        user_agent=(user_agent or "")[:300] or None,
        auth_level=auth_level,
    )
    db.add(session)
    await db.flush()
    max_age = int(lifetime.total_seconds())
    _set_cookies(response, settings, token, session.csrf_token, max_age=max_age)
    return session


def _set_cookies(
    response: Response, settings: Settings, token: str, csrf: str, *, max_age: int
) -> None:
    session_name, csrf_name = cookie_names(settings)
    if settings.cookie_secure:
        # Cookies issued before the prefix was adopted: replaced by the prefixed ones.
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True)
        response.delete_cookie(CSRF_COOKIE, path="/", secure=True)
    response.set_cookie(
        session_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Readable by the web app, which echoes it in the CSRF header on unsafe requests.
    response.set_cookie(
        csrf_name,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_cookies(response: Response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")
        # Deleting a __Host- cookie is itself a Set-Cookie, which must be Secure to be accepted.
        response.delete_cookie(HOST_PREFIX + name, path="/", secure=True)


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


async def load_principal(
    request: Request, db: AsyncSession, *, pending: bool = False
) -> Principal | None:
    """The signed-in account, or with ``pending`` the session still owing a second factor."""
    settings = get_settings()
    # The bare name is still read over HTTPS for sessions issued before the prefix; that
    # fallback can go once those have reached session_ttl_hours.
    token = request.cookies.get(cookie_names(settings)[0]) or request.cookies.get(SESSION_COOKIE)
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
                Session.auth_level == AUTH_PENDING if pending else Session.auth_level > 0,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    session, user = row
    if pending:
        return Principal(user=user, session=session)
    # The lifetimes follow the account's current role, so a session that outlived them (left
    # unused, or opened before the account became an administrator) ends here.
    absolute, idle = session_limits(settings, user)
    if now - session.last_seen_at > idle or now - session.created_at > absolute:
        return None
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


async def require_admin(principal: Annotated[Principal, Depends(require_principal)]) -> User:
    if not principal.user.is_admin:
        raise Forbidden("Administrators only.")
    # Administrators decide refunds and disputes: their sessions need the second factor.
    if principal.session.auth_level < AUTH_MFA:
        raise Forbidden(
            "Administration needs a sign-in with two-step verification.", code="mfa_required"
        )
    return principal.user


async def require_pending(request: Request, db: DbSession) -> Principal:
    """The session waiting for its second factor."""
    principal = await load_principal(request, db, pending=True)
    if principal is None:
        raise Unauthorized("Sign in again to continue.", code="mfa_session_expired")
    _check_csrf(request, principal.session)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
CurrentUser = Annotated[User, Depends(require_user)]
OptionalPrincipal = Annotated[Principal | None, Depends(optional_principal)]
AdminUser = Annotated[User, Depends(require_admin)]
PendingPrincipal = Annotated[Principal, Depends(require_pending)]
