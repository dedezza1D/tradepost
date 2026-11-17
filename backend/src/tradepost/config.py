from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    platform_fee_bps: int = Field(default=1000, ge=0, le=5000)
    reservation_ttl_minutes: int = 15

    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    mercadopago_access_token: SecretStr | None = None
    mercadopago_webhook_secret: SecretStr | None = None

    run_worker: bool = True
    log_json: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
