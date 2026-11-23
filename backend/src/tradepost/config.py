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
    # Session lifetimes (ASVS 7.1.1 asks for them to be documented). A session ends at the
    # absolute limit, or earlier when unused for the idle limit. Administrators, who decide
    # refunds and disputes, get the NIST SP 800-63B AAL2 timeouts.
    session_ttl_hours: int = 24 * 14
    session_idle_hours: int = 24 * 7
    admin_session_ttl_hours: int = 12
    admin_session_idle_minutes: int = 60

    # In production every request reaches the API through the web app's proxy, which adds this
    # header. Requests that bypass the proxy are rejected, so client-IP headers can be trusted.
    proxy_secret: SecretStr | None = None
    # Encrypts the authenticator secrets (TOTP) at rest: 32 random bytes, URL-safe base64.
    # Required in production; elsewhere a fixed development key is used.
    mfa_key: SecretStr | None = None
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

    # AI. "none" turns it off (the default and the live demo); "ollama" uses an open model on an
    # Ollama server; "fake" answers deterministically for tests.
    ai_provider: Literal["none", "ollama", "fake"] = "none"
    ai_base_url: str = "http://localhost:11434"
    ai_chat_model: str = "qwen2.5vl:7b"
    # Multilingual; beat nomic-embed-text on the Portuguese evaluation set (eval/results).
    ai_embed_model: str = "bge-m3"
    # Prices per million tokens. Zero for a local model; a paid model also needs a daily ceiling
    # (zero means no paid calls at all).
    ai_price_in_per_mtok: float = Field(default=0, ge=0)
    ai_price_out_per_mtok: float = Field(default=0, ge=0)
    ai_daily_budget_usd: float = Field(default=0, ge=0, le=100)

    # Hybrid search (used only when an embedding model is available): reciprocal rank fusion of
    # the full-text and vector rankings. Tuned on the dev half of eval/search_dataset.py.
    search_text_weight: float = Field(default=0.25, ge=0, le=10)
    search_vector_weight: float = Field(default=1.0, ge=0, le=10)
    search_rrf_k: int = Field(default=10, ge=1, le=500)
    # Cosine distance past which a vector match is dropped: every listing has *some* nearest
    # neighbour, and without a cutoff a query with no real match would still return the catalog.
    search_max_distance: float = Field(default=0.6, ge=0, le=2)

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

    # New passwords are checked against the Pwned Passwords corpus (see password_policy.py).
    breached_password_check: bool = True

    # Notifications outside the app. Web Push needs only a VAPID key pair (no account): the
    # private key, base64url; the public key is derived from it. Without one, push is not
    # offered. The subject tells push services who sends (a URL or mailto:); it defaults to the
    # public origin.
    vapid_private_key: SecretStr | None = None
    vapid_subject: str | None = None
    # E-mail: "none" sends nothing, "log" writes e-mails to the log (development), "smtp" sends
    # through any SMTP service.
    email_provider: Literal["none", "log", "smtp"] = "none"
    email_from: str = "noreply@example.com"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None

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
                    ("TP_MFA_KEY", self.mfa_key is not None),
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
