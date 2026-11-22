import httpx
import pytest
from pydantic import ValidationError

from tradepost.config import Settings, normalize_database_url
from tradepost.main import create_app


def test_hosted_database_urls_are_adapted_for_asyncpg() -> None:
    url = "postgresql://u:p@db.example.com/app?sslmode=require&channel_binding=require"
    assert normalize_database_url(url) == "postgresql+asyncpg://u:p@db.example.com/app?ssl=require"
    assert normalize_database_url("postgres://u:p@h/d").startswith("postgresql+asyncpg://")


def production_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        env="production",
        proxy_secret="s",
        cookie_secure=True,
        public_origin="https://shop.example",
        storage_backend="s3",
        _env_file=None,
    )


def test_production_refuses_unsafe_settings() -> None:
    with pytest.raises(ValidationError, match="TP_PROXY_SECRET"):
        Settings(env="production", _env_file=None)  # type: ignore[call-arg]
    assert production_settings().env == "production"


@pytest.mark.parametrize(("env", "status"), [("local", 200), ("production", 404)])
async def test_api_docs_are_off_in_production(env: str, status: int) -> None:
    settings = production_settings() if env == "production" else Settings(_env_file=None)  # type: ignore[call-arg]
    transport = httpx.ASGITransport(app=create_app(settings))
    headers = {"X-Proxy-Secret": "s"}
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for path in ("/api/docs", "/api/openapi.json"):
            assert (await client.get(path, headers=headers)).status_code == status
