from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.config import get_settings
from tradepost.deps import DbSession
from tradepost.errors import Conflict, Unauthorized
from tradepost.geo.service import resolve_city
from tradepost.identity import password_reset
from tradepost.identity.devices import issue_device_cookie, trusted_device
from tradepost.identity.models import User
from tradepost.identity.schemas import (
    ChangePasswordIn,
    LoginIn,
    MeOut,
    PasswordResetIn,
    PasswordResetRequestIn,
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
from tradepost.notifications.email import EmailSender
from tradepost.security import ratelimit
from tradepost.security.password_policy import check_new_password
from tradepost.security.passwords import hash_password, needs_rehash, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILURES = 10
LOGIN_FAILURE_WINDOW = 15 * 60
RESET_REQUESTS_PER_EMAIL = 3
RESET_REQUEST_WINDOW = 3600


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
    await check_new_password(
        request.app.state.http,
        body.password,
        email,
        check_breaches=get_settings().breached_password_check,
    )
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
    issue_device_cookie(response, user.id)
    return me_out(request, user)


@router.post(
    "/login",
    dependencies=[Depends(ratelimit.rate_limit("login", limit=20, window_seconds=60))],
)
async def login(body: LoginIn, request: Request, response: Response, db: DbSession) -> MeOut:
    email = body.email.lower()
    database = request.app.state.db
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # Failed sign-ins are limited per account (on top of the per-IP limit), which slows down
    # guessing one password from many addresses. A browser that signed in to this account
    # before is limited on its own failures instead, so failing on purpose from elsewhere
    # cannot lock the owner out. The check comes before the password is verified: past the
    # limit, an answer would tell right from wrong.
    account_key = f"login:email:{email}"
    device = trusted_device(request, user.id) if user is not None else None
    limit_key = f"login:device:{device}" if device else account_key
    await ratelimit.check(
        database, limit_key, limit=LOGIN_FAILURES, window_seconds=LOGIN_FAILURE_WINDOW
    )
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        await ratelimit.record(database, account_key, window_seconds=LOGIN_FAILURE_WINDOW)
        if device:
            await ratelimit.record(database, limit_key, window_seconds=LOGIN_FAILURE_WINDOW)
        raise Unauthorized("Invalid email or password.", code="invalid_credentials")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    await create_session(db, response, user, user_agent=request.headers.get("user-agent"))
    issue_device_cookie(response, user.id)
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
    body: ChangePasswordIn,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
    background: BackgroundTasks,
) -> None:
    user = principal.user
    if not verify_password(user.password_hash, body.current_password):
        raise Unauthorized("Current password is incorrect.", code="invalid_credentials")
    await check_new_password(
        request.app.state.http,
        body.new_password,
        user.email,
        check_breaches=get_settings().breached_password_check,
    )
    await _set_password(db, user, body.new_password)
    # Every other device is signed out; this one stays signed in.
    await revoke_all_sessions(db, user.id, except_id=principal.session.id)
    _tell_password_changed(request, background, user)


async def _set_password(db: AsyncSession, user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    user.password_changed_at = datetime.now(UTC)
    # A reset link asked for before the change must not undo it.
    await password_reset.forget_tokens(db, user.id)


def _email_sender(request: Request) -> EmailSender | None:
    sender: EmailSender | None = request.app.state.jobs.email
    return sender


def _tell_password_changed(request: Request, background: BackgroundTasks, user: User) -> None:
    """E-mail the owner once the response (and so the commit) is done."""
    background.add_task(
        password_reset.send_password_changed,
        _email_sender(request),
        get_settings().public_origin,
        to=user.email,
        name=user.display_name,
        locale=user.locale,
    )


@router.post(
    "/password-reset",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(ratelimit.rate_limit("password_reset", limit=10, window_seconds=3600))],
)
async def request_password_reset(
    body: PasswordResetRequestIn, request: Request, background: BackgroundTasks
) -> None:
    """E-mail a reset link if the address has an account that may have one. The answer is the
    same either way, and the work happens after it is sent (see ``password_reset``)."""
    sender = _email_sender(request)
    if sender is None or not sender.enabled:
        raise password_reset.EmailUnavailable("Passwords cannot be reset by e-mail now.")
    email = body.email.lower()
    # Per address too, so one inbox cannot be flooded from many IPs. Counted whether or not
    # the account exists: a 429 says nothing about it.
    await ratelimit.hit(
        request.app.state.db,
        f"password_reset:email:{email}",
        limit=RESET_REQUESTS_PER_EMAIL,
        window_seconds=RESET_REQUEST_WINDOW,
    )
    background.add_task(
        password_reset.send_reset_link,
        request.app.state.db,
        sender,
        get_settings().public_origin,
        email,
    )


@router.post(
    "/password-reset/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(ratelimit.rate_limit("password_reset_confirm", limit=20, window_seconds=900))
    ],
)
async def confirm_password_reset(
    body: PasswordResetIn,
    request: Request,
    response: Response,
    background: BackgroundTasks,
    db: DbSession,
) -> None:
    token, user = await password_reset.user_for_token(db, body.token)
    await check_new_password(
        request.app.state.http,
        body.new_password,
        user.email,
        check_breaches=get_settings().breached_password_check,
    )
    await password_reset.consume_token(db, token.id)
    await _set_password(db, user, body.new_password)
    # Whoever knew the old password is signed out everywhere, this browser included: the owner
    # signs in again with the new one. Having proved access to the inbox, this browser is
    # trusted for sign-in, so failures elsewhere cannot lock the owner out right now.
    await revoke_all_sessions(db, user.id)
    clear_cookies(response)
    issue_device_cookie(response, user.id)
    _tell_password_changed(request, background, user)
