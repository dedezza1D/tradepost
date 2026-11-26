import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from tradepost import jobs
from tradepost.ai.client import AI, build_provider
from tradepost.analytics import router as analytics_router
from tradepost.assistant import router as assistant_router
from tradepost.catalog.router import router as catalog_router
from tradepost.chat.router import router as chat_router
from tradepost.config import Settings, get_settings
from tradepost.coupons import router as coupons_router
from tradepost.db import Database
from tradepost.deps import DbSession
from tradepost.disputes import router as disputes_router
from tradepost.errors import install_error_handlers
from tradepost.fx import router as fx_router
from tradepost.geo.router import router as geo_router
from tradepost.identity.mfa_router import router as mfa_router
from tradepost.identity.router import router as identity_router
from tradepost.logs import configure_logging
from tradepost.media.storage import build_storage
from tradepost.middleware import (
    BodySizeLimitMiddleware,
    OriginCheckMiddleware,
    ProxyGuardMiddleware,
    RequestContextMiddleware,
)
from tradepost.moderation_queue import router as moderation_router
from tradepost.notifications.delivery import router as delivery_router
from tradepost.notifications.email import build_email_sender
from tradepost.notifications.router import router as notifications_router
from tradepost.notifications.service import Broker, asyncpg_dsn
from tradepost.notifications.webpush import WebPush
from tradepost.ops.router import router as ops_router
from tradepost.orders.router import router as orders_router
from tradepost.payments.registry import build_registry, drop_live_accounts
from tradepost.questions import router as questions_router
from tradepost.reviews.service import router as reviews_router
from tradepost.saved_searches import router as saved_searches_router
from tradepost.security.ratelimit import rate_limit
from tradepost.social.router import router as social_router


def build_push(settings: Settings) -> WebPush | None:
    """Web Push when a VAPID key is configured. Push services want a contact: a URL or mailto."""
    key = settings.vapid_private_key.get_secret_value() if settings.vapid_private_key else ""
    if not key:
        return None
    subject = settings.vapid_subject or (
        settings.public_origin
        if settings.public_origin.startswith("https://")
        else f"mailto:{settings.email_from}"
    )
    return WebPush(key, subject)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(settings)
        app.state.storage = build_storage(settings)
        app.state.payments = await drop_live_accounts(build_registry(settings))
        app.state.http = httpx.AsyncClient(headers={"User-Agent": "tradepost"})
        app.state.ai = AI(settings, app.state.db, build_provider(settings))
        app.state.jobs = jobs.JobContext(
            settings=settings,
            payments=app.state.payments,
            ai=app.state.ai,
            push=build_push(settings),
            email=build_email_sender(settings),
        )
        app.state.broker = Broker(asyncpg_dsn(settings.database_url))
        app.state.broker.start()
        worker: asyncio.Task[None] | None = None
        if settings.run_worker:
            worker = asyncio.create_task(jobs.run_worker(app.state.db, app.state.jobs))
        try:
            yield
        finally:
            if worker is not None:
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
            await app.state.broker.stop()
            await app.state.http.aclose()
            await app.state.db.dispose()

    # The interactive docs and the schema map every route; they stay off where the public is.
    show_docs = settings.env != "production"
    app = FastAPI(
        title="tradepost",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if show_docs else None,
        openapi_url="/api/openapi.json" if show_docs else None,
        redoc_url=None,
    )
    install_error_handlers(app)
    # Starlette runs the last-added middleware first.
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(OriginCheckMiddleware, settings=settings)
    app.add_middleware(ProxyGuardMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(identity_router)
    app.include_router(mfa_router)
    app.include_router(catalog_router)
    app.include_router(orders_router)
    app.include_router(reviews_router)
    app.include_router(geo_router)
    app.include_router(notifications_router)
    app.include_router(social_router)
    app.include_router(chat_router)
    app.include_router(questions_router)
    app.include_router(coupons_router)
    app.include_router(disputes_router)
    app.include_router(ops_router)
    app.include_router(saved_searches_router)
    app.include_router(assistant_router)
    app.include_router(fx_router)
    app.include_router(moderation_router)
    app.include_router(analytics_router)
    app.include_router(delivery_router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get(
        "/api/readyz",
        tags=["ops"],
        # Public and it queries the database: limited like any other route.
        dependencies=[Depends(rate_limit("readyz", limit=30, window_seconds=60))],
    )
    async def readyz(db: DbSession) -> dict[str, str]:
        """Readiness: the database answers."""
        await db.execute(text("SELECT 1"))
        return {"status": "ready"}

    if settings.storage_backend == "local":
        media_dir = Path(settings.media_local_dir)
        media_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/media", StaticFiles(directory=media_dir), name="media")

    return app
