"""Turning the second factor on and off, and replacing recovery codes (identity/mfa.py).

Every change asks for the password again. Turning the factor on or off, or using it for the
first time, starts a new session for this browser (new token, new level) and ends the other
sessions, which were signed in under the previous rules.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.deps import DbSession
from tradepost.errors import AppError, Conflict, NotFound, Unauthorized
from tradepost.identity import mfa
from tradepost.identity.models import TotpFactor, User
from tradepost.identity.router import check_second_factor
from tradepost.identity.schemas import (
    MfaCodeIn,
    MfaDisableIn,
    MfaSetupOut,
    MfaStatusOut,
    PasswordConfirmIn,
    RecoveryCodesOut,
)
from tradepost.identity.sessions import (
    AUTH_MFA,
    AUTH_PASSWORD,
    CurrentPrincipal,
    Principal,
    create_session,
    revoke_all_sessions,
)
from tradepost.security import ratelimit
from tradepost.security.passwords import verify_password

router = APIRouter(
    prefix="/api/auth/mfa",
    tags=["auth"],
    dependencies=[Depends(ratelimit.rate_limit("mfa_manage", limit=20, window_seconds=900))],
)


def _confirm_password(user: User, password: str) -> None:
    if not verify_password(user.password_hash, password):
        raise Unauthorized("The password is incorrect.", code="invalid_credentials")


async def _restart_session(
    db: AsyncSession, request: Request, response: Response, principal: Principal, level: int
) -> None:
    await revoke_all_sessions(db, principal.user.id)
    await create_session(
        db,
        response,
        principal.user,
        user_agent=request.headers.get("user-agent"),
        auth_level=level,
    )


@router.get("")
async def mfa_status(principal: CurrentPrincipal, db: DbSession) -> MfaStatusOut:
    factor = await mfa.confirmed_factor(db, principal.user.id)
    left = await mfa.recovery_codes_left(db, principal.user.id) if factor else 0
    return MfaStatusOut(enabled=factor is not None, recovery_codes_left=left)


@router.post("/setup")
async def setup(body: PasswordConfirmIn, principal: CurrentPrincipal, db: DbSession) -> MfaSetupOut:
    user = principal.user
    _confirm_password(user, body.password)
    if await mfa.confirmed_factor(db, user.id) is not None:
        raise Conflict("Two-step verification is already on.", code="mfa_already_enabled")
    secret = mfa.new_secret()
    factor = await db.get(TotpFactor, user.id, with_for_update=True)
    if factor is None:
        db.add(TotpFactor(user_id=user.id, secret_encrypted=mfa.encrypt_secret(secret)))
    else:
        factor.secret_encrypted = mfa.encrypt_secret(secret)
        factor.last_used_step = 0
    await db.flush()
    uri = mfa.provisioning_uri(secret, user.email)
    return MfaSetupOut(
        secret=mfa.secret_base32(secret), otpauth_uri=uri, qr_code=mfa.qr_data_uri(uri)
    )


@router.post("/confirm")
async def confirm(
    body: MfaCodeIn,
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    db: DbSession,
) -> RecoveryCodesOut:
    user = principal.user
    factor = await db.get(TotpFactor, user.id, with_for_update=True)
    if factor is None or factor.confirmed_at is not None:
        raise NotFound("Start the setup again.", code="mfa_setup_missing")
    database = request.app.state.db
    key = f"mfa:user:{user.id}"
    await ratelimit.check(database, key, limit=10, window_seconds=900)
    if not await mfa.use_totp_code(db, factor, body.code):
        await ratelimit.record(database, key, window_seconds=900)
        raise AppError("The code is not valid.", code="invalid_code")
    factor.confirmed_at = datetime.now(UTC)
    codes = await mfa.replace_recovery_codes(db, user.id)
    await _restart_session(db, request, response, principal, AUTH_MFA)
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable(
    body: MfaDisableIn,
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    db: DbSession,
) -> None:
    user = principal.user
    _confirm_password(user, body.password)
    await check_second_factor(request, db, user.id, body)
    await mfa.remove(db, user.id)
    await _restart_session(db, request, response, principal, AUTH_PASSWORD)


@router.post("/recovery-codes")
async def new_recovery_codes(
    body: PasswordConfirmIn, principal: CurrentPrincipal, db: DbSession
) -> RecoveryCodesOut:
    user = principal.user
    _confirm_password(user, body.password)
    if await mfa.confirmed_factor(db, user.id) is None:
        raise NotFound("Two-step verification is off.", code="mfa_not_enabled")
    return RecoveryCodesOut(recovery_codes=await mfa.replace_recovery_codes(db, user.id))
