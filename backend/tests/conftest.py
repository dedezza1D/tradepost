"""Test fixtures: a real PostgreSQL (Testcontainers, or TP_TEST_DATABASE_URL when given),
migrated with Alembic exactly as in production, and the ASGI app driven in-process."""

import io
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent
PRESERVED_TABLES = {"categories", "alembic_version", "municipalities"}


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    explicit = os.environ.get("TP_TEST_DATABASE_URL")
    if explicit:
        yield explicit
        return
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def settings_env(database_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    media_dir = tmp_path_factory.mktemp("media")
    env = {
        "TP_ENV": "test",
        "TP_DATABASE_URL": database_url,
        "TP_RUN_WORKER": "false",
        "TP_LOG_JSON": "false",
        "TP_MEDIA_LOCAL_DIR": str(media_dir),
        "TP_PUBLIC_ORIGIN": "http://testserver",
        # Tests never call real providers, whatever keys a local .env holds.
        "TP_PAYMENT_PROVIDERS": '["fake"]',
        # Tests that need the dispute window set it themselves.
        "TP_PAYOUT_HOLD_HOURS": "0",
    }
    previous = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    from tradepost.config import get_settings

    get_settings.cache_clear()
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrated(settings_env: None, database_url: str) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


@pytest.fixture
async def clean_db(migrated: None, database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        tables = (
            await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        ).scalars()
        names = [t for t in tables if t not in PRESERVED_TABLES]
        if names:
            await conn.execute(text(f"TRUNCATE {', '.join(names)} CASCADE"))
    await engine.dispose()


@pytest.fixture
async def app(clean_db: None) -> AsyncIterator[Any]:
    from tradepost.main import create_app

    application = create_app()
    async with application.router.lifespan_context(application):
        yield application


class ApiClient(AsyncClient):
    """httpx client that echoes the CSRF cookie in the header, like the web app does."""

    async def request(self, method: str, url: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        csrf = self.cookies.get("tp_csrf")
        if csrf and method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-CSRF-Token", csrf)
            kwargs["headers"] = headers
        return await super().request(method, url, **kwargs)


def make_client(app: Any) -> ApiClient:
    return ApiClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
async def client(app: Any) -> AsyncIterator[ApiClient]:
    async with make_client(app) as c:
        yield c


async def register(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    body = {
        "email": f"user-{uuid.uuid4().hex[:8]}@example.com",
        "password": "correct horse battery",
        "display_name": "Test User",
        "city": "Recife",
        "state": "PE",
        **overrides,
    }
    response = await client.post("/api/auth/register", json=body)
    assert response.status_code == 201, response.text
    return {**response.json(), "password": body["password"]}


@pytest.fixture
async def seller(app: Any) -> AsyncIterator[ApiClient]:
    async with make_client(app) as c:
        await register(c, display_name="Seller")
        yield c


@pytest.fixture
async def buyer(app: Any) -> AsyncIterator[ApiClient]:
    async with make_client(app) as c:
        await register(c, display_name="Buyer")
        yield c


def image_bytes(fmt: str = "PNG", size: tuple[int, int] = (64, 48), **save: Any) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, format=fmt, **save)
    return out.getvalue()


LISTING = {
    "title": "Bicicleta aro 29 seminova",
    "description": "Quadro de alumínio, freio a disco, pouco uso.",
    "category_slug": "bikes",
    "condition": "used",
    "price_cents": 120000,
    "stock": 1,
    "shipping_cents": 5000,
    "city": "Recife",
    "state": "PE",
}


async def create_listing(
    client: AsyncClient, *, publish: bool = True, **overrides: Any
) -> dict[str, Any]:
    response = await client.post("/api/listings", json={**LISTING, **overrides})
    assert response.status_code == 201, response.text
    listing: dict[str, Any] = response.json()
    if publish:
        photo = await client.post(
            f"/api/listings/{listing['id']}/photos",
            files={"file": ("p.png", image_bytes(), "image/png")},
        )
        assert photo.status_code == 201, photo.text
        published = await client.post(f"/api/listings/{listing['id']}/publish")
        assert published.status_code == 200, published.text
        listing = published.json()
    return listing
