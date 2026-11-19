"""Operations: request metrics, grouped errors, job tracking, alerts, switches and the AI client."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests.test_checkout import signed_in_client, sql
from tradepost import jobs
from tradepost.ai.client import AI, AIUnavailable, FakeProvider, usage_today
from tradepost.config import get_settings
from tradepost.ops.metrics import collector, flush, route_of
from tradepost.ops.service import notify_alerts, percentile


async def admin_client(app: Any) -> Any:
    client = await signed_in_client(app, "admin")
    me = (await client.get("/api/auth/me")).json()
    await sql(app, "UPDATE users SET is_admin = true WHERE id = :id", id=uuid.UUID(me["id"]))
    return client


async def flush_now(app: Any) -> None:
    async with app.state.db.sessionmaker() as db, db.begin():
        await flush(db)


def test_routes_are_grouped_by_template() -> None:
    assert route_of("/api/listings/3f1c2a52-0000-4000-8000-000000000001") == "/api/listings/{id}"
    assert route_of("/api/conversations/3f1c2a52-0000-4000-8000-000000000001/messages") == (
        "/api/conversations/{id}/messages"
    )
    assert route_of("/api/orders/12/ship") == "/api/orders/{n}/ship"


def test_percentiles_come_from_the_histogram() -> None:
    # 90 fast requests (<= 25 ms) and 10 slow ones (<= 500 ms).
    buckets = [90, 0, 0, 0, 10, 0, 0, 0]
    assert percentile(buckets, 0.5) == 25
    assert percentile(buckets, 0.95) == 500
    assert percentile([0] * 8, 0.5) is None


async def test_requests_are_counted_and_only_admins_see_them(app: Any) -> None:
    collector.drain()
    admin = await admin_client(app)
    try:
        for _ in range(3):
            await admin.get("/api/categories")
        await admin.get(f"/api/listings/{uuid.uuid4()}")  # 404
        await flush_now(app)
        ops = (await admin.get("/api/admin/ops")).json()
        routes = {(r["method"], r["route"]): r for r in ops["traffic"]["routes"]}
        assert routes[("GET", "/api/categories")]["count"] == 3
        assert routes[("GET", "/api/listings/{id}")]["count"] == 1
        assert ops["traffic"]["errors_4xx"] >= 1
        assert ops["traffic"]["p50_ms"] is not None
        assert ops["database"]["size_bytes"] > 0
        assert ops["ai"]["provider"] == "none"
        assert ops["ai"]["available"] is False
    finally:
        await admin.aclose()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as anon:
        assert (await anon.get("/api/admin/ops")).status_code == 401


async def test_unhandled_errors_are_grouped(app: Any) -> None:
    async def broken() -> None:
        raise RuntimeError("boom 42")

    app.add_api_route("/api/test-broken", broken, methods=["GET"])
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        for _ in range(2):
            response = await client.get("/api/test-broken")
            assert response.status_code == 500
            assert response.json()["error"]["code"] == "internal_error"
            assert "boom" not in response.text
    rows = await sql(app, "SELECT kind, route, count, message FROM error_events")
    assert rows == [("RuntimeError", "GET /api/test-broken", 2, "boom 42")]
    admin = await admin_client(app)
    try:
        listed = (await admin.get("/api/admin/ops")).json()["errors"]
        detail = (await admin.get(f"/api/admin/errors/{listed[0]['fingerprint']}")).json()
        assert "RuntimeError: boom 42" in detail["traceback"]
    finally:
        await admin.aclose()


async def test_failing_jobs_are_tracked_and_admins_are_alerted_once(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(db: Any, ctx: Any) -> int:
        raise ValueError("provider down")

    monkeypatch.setattr(jobs, "TASKS", {"payments_sync": failing, "fine": lambda db, ctx: _zero()})
    admin = await admin_client(app)
    try:
        for _ in range(3):
            await jobs.run_tick(app.state.db, app.state.jobs)
        state = {
            name: (failures, count)
            for name, failures, count in await sql(
                app, "SELECT name, consecutive_failures, last_count FROM job_runs"
            )
        }
        assert state == {"payments_sync": (3, 0), "fine": (0, 0)}
        assert await sql(app, "SELECT route FROM error_events") == [("job payments_sync",)]

        settings = get_settings()
        for _ in range(2):  # the second run does not announce the same alert again
            async with app.state.db.sessionmaker() as db, db.begin():
                await notify_alerts(db, settings)
        alerts = [
            n["payload"]["alert"]
            for n in (await admin.get("/api/notifications")).json()["items"]
            if n["kind"] == "ops_alert"
        ]
        assert alerts == ["job_failing:payments_sync"]
        ops = (await admin.get("/api/admin/ops")).json()
        assert [a["key"] for a in ops["alerts"]] == ["job_failing:payments_sync"]
    finally:
        await admin.aclose()


async def _zero() -> int:
    return 0


async def test_a_high_error_rate_raises_an_alert(app: Any) -> None:
    await sql(
        app,
        "INSERT INTO request_stats VALUES (date_trunc('minute', now()), 'GET', '/api/x',"
        " 40, 5, 0, 400, ARRAY[40,0,0,0,0,0,0,0])",
    )
    admin = await admin_client(app)
    try:
        ops = (await admin.get("/api/admin/ops")).json()
        assert "error_rate" in [a["key"] for a in ops["alerts"]]
    finally:
        await admin.aclose()


# --- AI client ---------------------------------------------------------------------------------


def fake_ai(app: Any, **overrides: Any) -> tuple[AI, FakeProvider]:
    settings = get_settings().model_copy(update={"ai_provider": "fake", **overrides})
    provider = FakeProvider()
    return AI(settings, app.state.db, provider), provider


async def test_ai_answers_are_cached_and_every_call_is_logged(app: Any) -> None:
    ai, provider = fake_ai(app)
    provider.responders["classify"] = lambda messages: {"category": "bikes"}
    schema = {"type": "object", "properties": {"category": {"type": "string"}}}
    async with app.state.db.sessionmaker() as db, db.begin():
        first = await ai.complete_json(
            db, "classify", "classify the item", "bicicleta aro 29", schema
        )
        second = await ai.complete_json(
            db, "classify", "classify the item", "bicicleta aro 29", schema
        )
        vectors = await ai.embed(db, "search", ["bicicleta aro 29", "bicicleta"])
    assert first == second == {"category": "bikes"}
    assert provider.calls == 2  # one chat (the second was cached) and one embedding call
    assert len(vectors) == 2
    rows = await sql(app, "SELECT feature, cache_hit, ok FROM ai_calls ORDER BY id")
    assert rows == [("classify", False, True), ("classify", True, True), ("search", False, True)]
    async with app.state.db.sessionmaker() as db:
        assert (await usage_today(db))["calls"] == 3


async def test_ai_can_be_switched_off_and_paid_models_need_a_budget(app: Any) -> None:
    ai, _ = fake_ai(app)
    admin = await admin_client(app)
    try:
        assert (await admin.put("/api/admin/flags/ai", json={"enabled": False})).status_code == 200
        assert (
            await admin.put("/api/admin/flags/nope", json={"enabled": False})
        ).status_code == 404
    finally:
        await admin.aclose()
    async with app.state.db.sessionmaker() as db:
        assert await ai.available(db) is False
        with pytest.raises(AIUnavailable):
            await ai.complete_json(db, "x", "x", "y", {"type": "object"})
    await sql(app, "UPDATE feature_flags SET enabled = true")

    paid, _ = fake_ai(app, ai_price_in_per_mtok=1.0, ai_daily_budget_usd=0)
    async with app.state.db.sessionmaker() as db:
        assert await paid.available(db) is False  # a ceiling of zero allows no paid calls
    budgeted, _ = fake_ai(app, ai_price_in_per_mtok=1.0, ai_daily_budget_usd=1.0)
    async with app.state.db.sessionmaker() as db:
        assert await budgeted.available(db) is True
        await db.execute(
            text(
                "INSERT INTO ai_calls (feature, provider, model, input_tokens, output_tokens,"
                " latency_ms, cost_usd, cache_hit, ok) VALUES ('x', 'fake', 'm', 0, 0, 0, 1.0,"
                " false, true)"
            )
        )
        await db.commit()
        assert await budgeted.available(db) is False
