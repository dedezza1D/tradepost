"""Trusted-device cookie for sign-in.

Failed sign-ins are limited per account, which stops guessing one password from many
addresses, but would also let anyone lock the owner out by failing on purpose. A browser that
has signed in to the account before carries a signed cookie naming that account; for it the
account-wide limit is replaced by a limit on its own failures, so the owner keeps access during
an attack while new devices stay under the account limit (OWASP: device cookies).

The cookie is stateless: ``<user id>.<issued at>.<HMAC>``. It grants nothing by itself; it only
decides which failure counter applies.
"""

import hashlib
import hmac
import secrets
import time
import uuid
from datetime import timedelta

from fastapi import Request, Response

from tradepost.config import Settings, get_settings

DEVICE_COOKIE = "tp_device"
DEVICE_TTL = timedelta(days=180)
_LABEL = b"tradepost sign-in device cookie"
# Used where no proxy secret exists (local development and tests): cookies last one process.
_PROCESS_KEY = secrets.token_bytes(32)


def _key(settings: Settings) -> bytes:
    secret = settings.proxy_secret
    if secret is None:
        return _PROCESS_KEY
    # A key of its own, derived from the deployment's secret (HMAC with a fixed label).
    return hmac.new(secret.get_secret_value().encode(), _LABEL, hashlib.sha256).digest()


def _sign(settings: Settings, payload: str) -> str:
    return hmac.new(_key(settings), payload.encode(), hashlib.sha256).hexdigest()


def issue_device_cookie(response: Response, user_id: uuid.UUID) -> None:
    settings = get_settings()
    payload = f"{user_id}.{int(time.time())}"
    response.set_cookie(
        DEVICE_COOKIE,
        f"{payload}.{_sign(settings, payload)}",
        max_age=int(DEVICE_TTL.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/auth",
    )


def trusted_device(request: Request, user_id: uuid.UUID) -> str | None:
    """An id for this browser when it carries a valid cookie for ``user_id``, else None."""
    value = request.cookies.get(DEVICE_COOKIE, "")
    parts = value.split(".")
    if len(parts) != 3 or len(value) > 200:
        return None
    owner, issued, signature = parts
    expected = _sign(get_settings(), f"{owner}.{issued}")
    if not hmac.compare_digest(signature, expected) or owner != str(user_id):
        return None
    if not issued.isdigit() or time.time() - int(issued) > DEVICE_TTL.total_seconds():
        return None
    return signature[:32]
