"""Request body ceilings per route, and the rate limit on the readiness probe."""

import time
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from tests.conftest import make_client
from tradepost.config import get_settings


async def chunks(total: int, size: int = 64 * 1024) -> AsyncIterator[bytes]:
    """A body sent without Content-Length (chunked), so only counting can stop it."""
    sent = 0
    while sent < total:
        part = min(size, total - sent)
        sent += part
        yield b"x" * part


async def test_a_declared_length_over_the_ceiling_is_refused_before_parsing(app: Any) -> None:
    async with make_client(app) as anon:
        response = await anon.post(
            "/api/auth/login",
            content=b"{" + b" " * (200 * 1024) + b"}",
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


async def test_a_chunked_body_is_cut_off_at_the_ceiling(app: Any) -> None:
    async with make_client(app) as anon:
        response = await anon.post(
            "/api/auth/login",
            content=chunks(200 * 1024),
            headers={"content-type": "application/json"},
        )
        webhook = await anon.post("/api/webhooks/fake", content=chunks(300 * 1024))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert webhook.status_code == 413


async def test_photo_routes_allow_a_photo_and_nothing_bigger(app: Any) -> None:
    photo_limit = get_settings().max_photo_bytes
    path = f"/api/listings/{uuid.uuid4()}/photos"
    files = {"file": ("p.jpg", b"x" * (photo_limit - 1024), "image/jpeg")}
    oversize = {"file": ("p.jpg", b"x" * (photo_limit + 128 * 1024), "image/jpeg")}
    async with make_client(app) as anon:
        # Within the ceiling the request reaches the route, which asks for a session.
        assert (await anon.post(path, files=files)).status_code == 401
        assert (await anon.post("/api/me/avatar", files=files)).status_code == 401
        assert (await anon.post(path, files=oversize)).status_code == 413


async def test_readiness_probe_is_rate_limited(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from tradepost.security import ratelimit

    # Frozen clock for the limiter, so the requests cannot straddle two fixed windows.
    frozen = float(int(time.time()) // 60 * 60)
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(time=lambda: frozen))
    async with make_client(app) as anon:
        statuses = [(await anon.get("/api/readyz")).status_code for _ in range(31)]
    assert set(statuses[:30]) == {200}
    assert statuses[30] == 429
