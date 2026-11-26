"""Second factor: an authenticator app (TOTP, RFC 6238) with single-use recovery codes.

- The shared secret (160 random bits) is stored encrypted with AES-GCM under ``TP_MFA_KEY``.
- Codes are six digits for 30-second steps; the current and the previous step are accepted
  (clock drift), and a step once used is never accepted again (replay).
- Ten recovery codes are shown once when the factor is turned on (or replaced on request); only
  their SHA-256 is stored and each works once.
- Failed codes are limited per account like failed passwords (identity/router.py).
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

import segno
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.config import Settings, get_settings
from tradepost.identity.models import RecoveryCode, TotpFactor

ISSUER = "tradepost"
STEP_SECONDS = 30
DIGITS = 6
RECOVERY_CODES = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
_AAD = b"tradepost totp secret"
# Development and tests only: production requires TP_MFA_KEY (config.py).
_DEV_KEY = hashlib.sha256(b"tradepost local development mfa key").digest()


def _key(settings: Settings) -> bytes:
    if settings.mfa_key is None:
        return _DEV_KEY
    key = base64.urlsafe_b64decode(settings.mfa_key.get_secret_value())
    if len(key) != 32:
        raise ValueError("TP_MFA_KEY must be 32 bytes, URL-safe base64")
    return key


def encrypt_secret(secret: bytes) -> bytes:
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(_key(get_settings())).encrypt(nonce, secret, _AAD)


def decrypt_secret(blob: bytes) -> bytes:
    return AESGCM(_key(get_settings())).decrypt(blob[:12], blob[12:], _AAD)


def new_secret() -> bytes:
    return secrets.token_bytes(20)


def code_at(secret: bytes, step: int) -> str:
    digest = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def matching_step(secret: bytes, code: str, *, after: int, now: float | None = None) -> int | None:
    """The step a code belongs to (current or previous), if it is right and newer than
    ``after``."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in (step, step - 1):
        if candidate > after and hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def secret_base32(secret: bytes) -> str:
    return base64.b32encode(secret).decode().rstrip("=")


def provisioning_uri(secret: bytes, email: str) -> str:
    label = quote(f"{ISSUER}:{email}", safe=":@")
    query = urlencode(
        {
            "secret": secret_base32(secret),
            "issuer": ISSUER,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": STEP_SECONDS,
        }
    )
    return f"otpauth://totp/{label}?{query}"


def qr_data_uri(uri: str) -> str:
    """The QR code as an SVG data URI (served to an <img>, so no markup is injected)."""
    return str(segno.make(uri, error="m").svg_data_uri(scale=5, border=2))


def _normalize_recovery(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def _hash_recovery(code: str) -> bytes:
    return hashlib.sha256(_normalize_recovery(code).encode()).digest()


def _new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


async def confirmed_factor(
    db: AsyncSession, user_id: uuid.UUID, *, lock: bool = False
) -> TotpFactor | None:
    """The account's confirmed factor. ``lock`` holds its row, so two requests cannot accept
    the same code."""
    factor = await db.get(TotpFactor, user_id, with_for_update=lock)
    return factor if factor is not None and factor.confirmed_at is not None else None


async def replace_recovery_codes(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODES)]
    db.add_all(RecoveryCode(user_id=user_id, code_hash=_hash_recovery(c)) for c in codes)
    await db.flush()
    return codes


async def recovery_codes_left(db: AsyncSession, user_id: uuid.UUID) -> int:
    rows = await db.execute(
        select(RecoveryCode.id).where(
            RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None)
        )
    )
    return len(rows.all())


async def use_recovery_code(db: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    row = (
        await db.execute(
            select(RecoveryCode)
            .where(
                RecoveryCode.user_id == user_id,
                RecoveryCode.code_hash == _hash_recovery(code),
                RecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    row.used_at = datetime.now(UTC)
    return True


async def use_totp_code(db: AsyncSession, factor: TotpFactor, code: str) -> bool:
    step = matching_step(decrypt_secret(factor.secret_encrypted), code, after=factor.last_used_step)
    if step is None:
        return False
    factor.last_used_step = step
    return True


async def verify_second_factor(
    db: AsyncSession, factor: TotpFactor, *, code: str | None, recovery_code: str | None
) -> bool:
    if code:
        return await use_totp_code(db, factor, code)
    if recovery_code:
        return await use_recovery_code(db, factor.user_id, recovery_code)
    return False


async def remove(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    await db.execute(delete(TotpFactor).where(TotpFactor.user_id == user_id))
