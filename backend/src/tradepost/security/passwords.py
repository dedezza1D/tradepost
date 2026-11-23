from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP-recommended Argon2id parameters (m=19 MiB, t=2, p=1).
_hasher = PasswordHasher(time_cost=2, memory_cost=19 * 1024, parallelism=1)

# Verified against when the email is unknown, so a login attempt costs the same either way
# and response time does not reveal whether an account exists.
_DUMMY_HASH = _hasher.hash("tradepost-dummy-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def has_usable_password(password_hash: str) -> bool:
    """False for accounts that cannot sign in with a password (seeded with a non-hash)."""
    return password_hash.startswith("$argon2")
