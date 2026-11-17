import pytest
from pydantic import ValidationError

from tradepost.config import Settings, normalize_database_url


def test_hosted_database_urls_are_adapted_for_asyncpg() -> None:
    url = "postgresql://u:p@db.example.com/app?sslmode=require&channel_binding=require"
    assert normalize_database_url(url) == "postgresql+asyncpg://u:p@db.example.com/app?ssl=require"
    assert normalize_database_url("postgres://u:p@h/d").startswith("postgresql+asyncpg://")


def test_production_refuses_unsafe_settings() -> None:
    with pytest.raises(ValidationError, match="TP_PROXY_SECRET"):
        Settings(env="production", _env_file=None)  # type: ignore[call-arg]
    ok = Settings(  # type: ignore[call-arg]
        env="production",
        proxy_secret="s",
        cookie_secure=True,
        public_origin="https://shop.example",
        storage_backend="s3",
        _env_file=None,
    )
    assert ok.env == "production"
