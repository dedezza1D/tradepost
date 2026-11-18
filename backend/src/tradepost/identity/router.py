from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tradepost.deps import DbSession
from tradepost.errors import AppError, Conflict, Unauthorized
from tradepost.geo.service import resolve_city
from tradepost.identity.models import User
from tradepost.identity.schemas import (
    ChangePasswordIn,
    LoginIn,
    MeOut,
    ProfileUpdateIn,
    RegisterIn,
)
from tradepost.identity.sessions import (
    CurrentPrincipal,
    CurrentUser,
    OptionalPrincipal,
    clear_cookies,
    create_session,
    revoke_all_sessions,
    revoke_session,
)
from tradepost.security import ratelimit
from tradepost.security.passwords import hash_password, needs_rehash, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def me_out(request: Request, user: User) -> MeOut:
    storage = request.app.state.storage
    out = MeOut.model_validate(user)
    out.avatar_url = storage.url(user.avatar_key) if user.avatar_key else None
    return out


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(ratelimit.rate_limit("register", limit=10, window_seconds=3600))],
)
async def register(body: RegisterIn, request: Request, response: Response, db: DbSession) -> MeOut:
    email = body.email.lower()
    if body.password.lower() == email:
        raise AppError("Password must not be your email.", code="weak_password")
    user = User(
        email=email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        locale=body.locale,
    )
    if body.city and body.state:
        await _set_city(db, user, body.city, body.state)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise Conflict("This email is already registered.", code="email_taken") from None
    await create_session(db, response, user, user_agent=request.headers.get("user-agent"))
    return me_out(request, user)


@router.post(
    "/login",
    dependencies=[Depends(ratelimit.rate_limit("login", limit=20, window_seconds=60))],
)
async def login(body: LoginIn, request: Request, response: Response, db: DbSession) -> MeOut:
    email = body.email.lower()
    # Per-account limit on top of the per-IP one: slows down guessing one victim's password
    # from many addresses.
    await ratelimit.hit(request.app.state.db, f"login:email:{email}", limit=10, window_seconds=900)
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        raise Unauthorized("Invalid email or password.", code="invalid_credentials")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    await create_session(db, response, user, user_agent=request.headers.get("user-agent"))
    return me_out(request, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: OptionalPrincipal, response: Response, db: DbSession) -> None:
    if principal is not None:
        await revoke_session(db, principal.session.id)
    clear_cookies(response)


@router.get("/me")
async def me(request: Request, user: CurrentUser) -> MeOut:
    return me_out(request, user)


@router.patch("/me")
async def update_profile(
    body: ProfileUpdateIn, request: Request, user: CurrentUser, db: DbSession
) -> MeOut:
    changes = body.model_dump(exclude_unset=True)
    if "display_name" in changes and body.display_name:
        user.display_name = body.display_name
    if body.locale:
        user.locale = body.locale
    if "bio" in changes:
        user.bio = body.bio or None
    if "city" in changes or "state" in changes:
        city, state = changes.get("city", user.city), changes.get("state", user.state)
        if city and state:
            await _set_city(db, user, city, state)
        else:
            user.city, user.state, user.municipality_id = None, None, None
    return me_out(request, user)


async def _set_city(db: DbSession, user: User, city: str, state: str) -> None:
    """Store the canonical city (spelling from the IBGE list) and its code."""
    municipality = await resolve_city(db, city, state)
    user.city, user.state, user.municipality_id = (
        municipality.name,
        municipality.state,
        municipality.ibge_code,
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(ratelimit.rate_limit("password", limit=10, window_seconds=900))],
)
async def change_password(
    body: ChangePasswordIn, principal: CurrentPrincipal, db: DbSession
) -> None:
    user = principal.user
    if not verify_password(user.password_hash, body.current_password):
        raise Unauthorized("Current password is incorrect.", code="invalid_credentials")
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = datetime.now(UTC)
    # Every other device is signed out; this one stays signed in.
    await revoke_all_sessions(db, user.id, except_id=principal.session.id)
