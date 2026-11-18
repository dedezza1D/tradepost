from functools import lru_cache
from typing import Literal, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Accept the URL a hosting provider hands out (``postgres://...?sslmode=require``) and turn
    it into what SQLAlchemy's asyncpg driver expects."""
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    query = []
    for key, value in parse_qsl(parts.query):
        if key == "sslmode":
            query.append(("ssl", value))
        elif key in ("channel_binding",):
            continue  # libpq-only option
        else:
            query.append((key, value))
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables prefixed with ``TP_``."""

    model_config = SettingsConfigDict(env_prefix="TP_", env_file=".env", extra="ignore")

    env: Literal["local", "test", "production"] = "local"
    database_url: str = "postgresql+asyncpg://tradepost:tradepost@localhost:5433/tradepost"
    db_pool_size: int = 5

    # Public origin of the web app. Unsafe requests must come from it (CSRF defence in depth).
    public_origin: str = "http://localhost:5180"
    # Cookies are Secure everywhere except plain-HTTP local development.
    cookie_secure: bool = False
    session_ttl_hours: int = 24 * 14

    # In production every request reaches the API through the web app's proxy, which adds this
    # header. Requests that bypass the proxy are rejected, so client-IP headers can be trusted.
    proxy_secret: SecretStr | None = None
    client_ip_header: str | None = None

    storage_backend: Literal["local", "s3"] = "local"
    media_local_dir: str = "./var/media"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "tradepost-media"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    media_public_base_url: str = "/media"

    max_photo_bytes: int = 5 * 1024 * 1024
    max_photos_per_listing: int = 8
    max_image_pixels: int = 40_000_000
    # Hard ceilings that keep object storage inside the provider's free tier (R2: 10 GB).
    # Uploads beyond them are refused; nothing is ever billed.
    media_quota_bytes: int = 2 * 1024**3
    max_photos_per_user: int = 200

    platform_fee_bps: int = Field(default=1000, ge=0, le=5000)
    reservation_ttl_minutes: int = 15
    # Card installments accepted (Mercado Pago and the test provider; Stripe charges at once).
    max_installments: int = Field(default=12, ge=1, le=12)
    # Seller payouts wait for the dispute window to close after delivery.
    payout_hold_hours: int = Field(default=168, ge=0, le=24 * 60)

    # Providers offered at checkout, among those with credentials configured.
    payment_providers: list[str] = ["stripe", "mercadopago"]
    # Sandbox conveniences (Mercado Pago auto-approves test Pix). Live keys are always refused.
    payments_sandbox: bool = True

    stripe_secret_key: SecretStr | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: SecretStr | None = None
    mercadopago_access_token: SecretStr | None = None
    mercadopago_public_key: str | None = None
    mercadopago_webhook_secret: SecretStr | None = None

    # Multiplies every rate limit. Only end-to-end tests raise it (many sign-ups from one IP).
    rate_limit_scale: float = Field(default=1.0, ge=1.0, le=1000.0)

    run_worker: bool = True
    log_json: bool = True

    @field_validator("database_url")
    @classmethod
    def _database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @model_validator(mode="after")
    def _production_guards(self) -> Self:
        if self.env == "production":
            missing = [
                name
                for name, ok in (
                    ("TP_PROXY_SECRET", self.proxy_secret is not None),
                    ("TP_COOKIE_SECURE=true", self.cookie_secure),
                    ("TP_PUBLIC_ORIGIN (https)", self.public_origin.startswith("https://")),
                    ("TP_STORAGE_BACKEND=s3", self.storage_backend == "s3"),
                )
                if not ok
            ]
            if missing:
                raise ValueError(f"production settings missing: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
