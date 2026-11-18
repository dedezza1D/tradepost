"""Locations, locale and notifications."""

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest

from tests.conftest import create_listing, make_client, register
from tests.test_checkout import checkout, fake, pay, webhook
from tradepost.notifications import router as notifications_router
from tradepost.notifications.service import Broker

RECIFE, OLINDA, SAO_PAULO = 2611606, 2609600, 3550308


# --- locations ------------------------------------------------------------------------------


async def test_city_autocomplete_ignores_accents_and_case(client: Any) -> None:
    found = (await client.get("/api/geo/cities", params={"q": "SAO PAU", "state": "sp"})).json()
    assert found[0] == {"ibge_code": SAO_PAULO, "name": "São Paulo", "state": "SP"}


async def test_listing_city_must_exist_and_is_stored_canonically(seller: Any) -> None:
    from tests.conftest import LISTING

    bad = await seller.post("/api/listings", json={**LISTING, "city": "Atlantis", "state": "PE"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "unknown_city"
    good = await seller.post("/api/listings", json={**LISTING, "city": "  recife ", "state": "pe"})
    assert good.status_code == 201
    assert (good.json()["city"], good.json()["state"]) == ("Recife", "PE")


async def test_near_search_filters_by_radius_and_orders_by_distance(
    seller: Any, client: Any
) -> None:
    await create_listing(seller, title="Bicicleta em Olinda", city="Olinda", state="PE")
    await create_listing(seller, title="Bicicleta em Recife", city="Recife", state="PE")
    await create_listing(seller, title="Bicicleta em São Paulo", city="São Paulo", state="SP")

    near = await client.get(
        "/api/listings", params={"near": RECIFE, "radius_km": 50, "sort": "nearest"}
    )
    items = near.json()["items"]
    assert [i["title"] for i in items] == ["Bicicleta em Recife", "Bicicleta em Olinda"]
    assert items[0]["distance_km"] == 0.0
    assert 3 < items[1]["distance_km"] < 15

    wide = await client.get("/api/listings", params={"near": RECIFE, "radius_km": 500})
    assert len(wide.json()["items"]) == 2  # São Paulo is ~2,100 km away
    plain = await client.get("/api/listings")
    assert all(i["distance_km"] is None for i in plain.json()["items"])


async def test_cep_lookup(app: Any, client: Any) -> None:
    def viacep(request: httpx.Request) -> httpx.Response:
        if "50030230" in str(request.url):
            return httpx.Response(200, json={"cep": "50030-230", "ibge": str(RECIFE)})
        return httpx.Response(200, json={"erro": "true"})

    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(viacep))
    found = await client.get("/api/geo/cep/50030-230")
    assert found.json() == {"ibge_code": RECIFE, "name": "Recife", "state": "PE"}
    missing = await client.get("/api/geo/cep/99999999")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "cep_not_found"
    malformed = await client.get("/api/geo/cep/123")
    assert malformed.json()["error"]["code"] == "invalid_cep"


# --- locale and profile city ------------------------------------------------------------------


async def test_locale_and_city_on_the_profile(client: Any) -> None:
    me = await register(client, locale="en", city="olinda", state="PE")
    assert (me["locale"], me["city"], me["municipality_id"]) == ("en", "Olinda", OLINDA)
    updated = await client.patch("/api/auth/me", json={"locale": "pt-BR"})
    assert updated.json()["locale"] == "pt-BR"
    invalid = await client.patch("/api/auth/me", json={"locale": "fr"})
    assert invalid.status_code == 422


# --- notifications ------------------------------------------------------------------------------


async def test_a_sale_notifies_seller_and_buyer(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, title="Teclado mecânico", price_cents=10_000)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"])
    ref = next(iter(fake(app).payments))
    fake(app).settle(ref)
    await webhook(app, ref)

    seller_feed = (await seller.get("/api/notifications")).json()
    buyer_feed = (await buyer.get("/api/notifications")).json()
    assert [n["kind"] for n in seller_feed["items"]] == ["order_paid"]
    assert seller_feed["items"][0]["payload"]["title"] == "Teclado mecânico"
    assert seller_feed["unread"] == 1
    assert [n["kind"] for n in buyer_feed["items"]] == ["purchase_confirmed"]

    order_id = seller_feed["items"][0]["payload"]["order_id"]
    await seller.post(f"/api/orders/{order_id}/ship", json={"tracking_code": "BR12345678"})
    assert [n["kind"] for n in (await buyer.get("/api/notifications")).json()["items"]] == [
        "order_shipped",
        "purchase_confirmed",
    ]

    marked = await seller.post("/api/notifications/read", json={"all": True})
    assert marked.json() == {"unread": 0}


async def test_notifications_are_private(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"])
    ref = next(iter(fake(app).payments))
    fake(app).settle(ref)
    await webhook(app, ref)
    own = (await seller.get("/api/notifications")).json()["items"][0]["id"]
    # Marking someone else's notification as read does nothing.
    await buyer.post("/api/notifications/read", json={"ids": [own]})
    assert (await seller.get("/api/notifications")).json()["unread"] == 1


async def test_stream_requires_a_session(client: Any) -> None:
    assert (await client.get("/api/stream")).status_code == 401


def _events(body: str) -> list[tuple[str, Any]]:
    out = []
    for block in body.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if ": " in line and line[0] != ":"
        )
        if "event" in fields:
            out.append((fields["event"], json.loads(fields["data"])))
    return out


async def test_stream_replays_missed_notifications_after_reconnect(
    app: Any, seller: Any, buyer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifications_router, "STREAM_SECONDS", 0.2)
    monkeypatch.setattr(notifications_router, "HEARTBEAT_SECONDS", 0.1)
    item = await create_listing(seller)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"])
    ref = next(iter(fake(app).payments))
    fake(app).settle(ref)
    await webhook(app, ref)

    # A client that last saw event 0 gets everything after it, then the unread count.
    response = await seller.get("/api/stream", headers={"Last-Event-ID": "0"})
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert events[0] == ("unread", {"count": 1})
    assert events[1][0] == "notification"
    assert events[1][1]["kind"] == "order_paid"

    # A fresh connection does not replay history.
    fresh = _events((await seller.get("/api/stream")).text)
    assert [e[0] for e in fresh] == ["unread"]


async def test_broker_wakes_only_that_users_streams() -> None:
    broker = Broker("postgresql://unused")
    alice, bob = uuid.uuid4(), uuid.uuid4()
    a, b = broker.subscribe(alice), broker.subscribe(bob)
    broker._wake(None, 0, "tp_notifications", str(alice))
    await asyncio.sleep(0)
    assert a.is_set()
    assert not b.is_set()
    broker.unsubscribe(alice, a)
    broker.unsubscribe(bob, b)


async def test_register_rejects_unknown_city(app: Any) -> None:
    async with make_client(app) as c:
        response = await c.post(
            "/api/auth/register",
            json={
                "email": f"x-{uuid.uuid4().hex[:6]}@example.com",
                "password": "long enough password",
                "display_name": "Ana",
                "city": "Nowhere",
                "state": "PE",
            },
        )
    assert response.json()["error"]["code"] == "unknown_city"
