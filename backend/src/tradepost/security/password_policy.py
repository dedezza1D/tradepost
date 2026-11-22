"""Password rules for new passwords (sign-up and password change), after NIST SP 800-63B-4.

- At least 15 characters while the password is the only factor; no composition rules and no
  periodic change.
- Not built from the account or the service: the e-mail, its local part, "tradepost".
- Not a run of one or two characters ("aaaaaaaaaaaaaaa", "abababababababab").
- Not in the Pwned Passwords breach corpus. Only the first five hex characters of the
  password's SHA-1 leave the server (k-anonymity), with padding so the answer size reveals
  nothing; when the service is slow or down the check is skipped, never the sign-up.

Existing passwords keep working: the rules apply when a password is set.
"""

import hashlib

import httpx

from tradepost.errors import AppError
from tradepost.logs import error_summary, get_logger

log = get_logger(__name__)

MIN_LENGTH = 15
MAX_LENGTH = 128
PWNED_RANGE_URL = "https://api.pwnedpasswords.com/range/"
PWNED_TIMEOUT_SECONDS = 2.0
_SERVICE_WORDS = ("tradepost",)


class WeakPassword(AppError):
    status_code = 422
    code = "weak_password"


class BreachedPassword(AppError):
    status_code = 422
    code = "breached_password"


def check_context(password: str, email: str) -> None:
    """Refuse passwords made from the account or the service, or from a tiny alphabet."""
    lowered = password.lower()
    local = email.lower().split("@", 1)[0]
    if lowered == email.lower() or (len(local) >= 4 and local in lowered):
        raise WeakPassword("The password must not contain your email.")
    if any(word in lowered for word in _SERVICE_WORDS):
        raise WeakPassword("The password must not contain the name of the site.")
    if len(set(lowered)) <= 2:
        raise WeakPassword("The password repeats too few characters.")


async def is_breached(http: httpx.AsyncClient, password: str) -> bool | None:
    """True when the password appears in the breach corpus, None when that is unknown."""
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        response = await http.get(
            PWNED_RANGE_URL + prefix,
            headers={"Add-Padding": "true"},
            timeout=PWNED_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("pwned_passwords_unavailable", error=error_summary(exc))
        return None
    for line in response.text.splitlines():
        candidate, _, count = line.strip().partition(":")
        # Padding entries carry a count of zero.
        if candidate == suffix and count.strip() not in ("", "0"):
            return True
    return False


async def check_new_password(
    http: httpx.AsyncClient, password: str, email: str, *, check_breaches: bool
) -> None:
    check_context(password, email)
    if check_breaches and await is_breached(http, password):
        raise BreachedPassword("This password appeared in a data breach. Choose another one.")
