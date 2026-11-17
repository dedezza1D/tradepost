"""Checkout, payment and order flows against the fake provider and a real PostgreSQL."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from tests.conftest import create_listing, make_client, register
from tradepost.jobs import run_tick
from tradepost.orders.models import AttemptStatus
from tradepost.payments.fake import FakeProvider, sign


def fake(app: Any) -> FakeProvider:
    provider: FakeProvider = app.state.payments.get("fake")
    return provider


async def checkout(client: Any, items: list[tuple[str, int]], key: str | None = None) -> Any:
    return await client.post(
        "/api/checkouts",
        json={
            "provider": "fake",
            "items": [{"listing_id": lid, "quantity": q} for lid, q in items],
        },
        headers={"Idempotency-Key": key or uuid.uuid4().hex},
    )


async def pay(client: Any, checkout_id: str, method: str = "pix", **card: Any) -> Any:
    body: dict[str, Any] = {"method": method}
    if card:
        body["card"] = card
    return await client.post(f"/api/checkouts/{checkout_id}/payments", json=body)


async def webhook(app: Any, provider_ref: str, event_id: str | None = None) -> Any:
    body = json.dumps(
        {"id": event_id or uuid.uuid4().hex, "type": "payment.updated", "payment": provider_ref}
    ).encode()
    async with make_client(app) as anon:
        return await anon.post(
            "/api/webhooks/fake",
            content=body,
            headers={"x-fake-signature": sign(body), "content-type": "application/json"},
        )


async def sql(app: Any, statement: str, **params: Any) -> Any:
    async with app.state.db.sessionmaker() as db, db.begin():
        result = await db.execute(text(statement), params)
        return result.all() if result.returns_rows else None


async def signed_in_client(app: Any, name: str) -> Any:
    """A buyer created straight in the database: registering 25 users through the API would
    trip the per-IP sign-up limit (which is the point of that limit)."""
    from tradepost.identity.models import Session, User
    from tradepost.identity.sessions import _hash_token

    token, csrf = uuid.uuid4().hex, uuid.uuid4().hex
    async with app.state.db.sessionmaker() as db, db.begin():
        user = User(
            email=f"{name}-{uuid.uuid4().hex[:6]}@example.com", password_hash="x", display_name=name
        )
        db.add(user)
        await db.flush()
        db.add(
            Session(
                user_id=user.id,
                token_hash=_hash_token(token),
                csrf_token=csrf,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
    client = make_client(app)
    client.cookies.set("tp_session", token)
    client.cookies.set("tp_csrf", csrf)
    return client


async def tick(app: Any) -> dict[str, int]:
    return await run_tick(app.state.db, app.state.jobs)


async def paid_checkout(app: Any, seller: Any, buyer: Any, **listing: Any) -> dict[str, Any]:
    item = await create_listing(seller, **listing)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    attempt = (await pay(buyer, created["id"])).json()["attempts"][0]
    ref = list(fake(app).payments)[-1]
    fake(app).settle(ref)
    assert (await webhook(app, ref)).status_code == 200
    result = (await buyer.get(f"/api/checkouts/{created['id']}")).json()
    assert result["status"] == "paid", result
    return {"listing": item, "checkout": result, "attempt": attempt}


# --- reservation --------------------------------------------------------------------------


async def test_checkout_reserves_stock_and_splits_orders_per_seller(
    app: Any, seller: Any, buyer: Any
) -> None:
    other_seller = make_client(app)
    await register(other_seller, display_name="Other seller")
    a = await create_listing(seller, price_cents=10_000, shipping_cents=1_500, stock=3)
    b = await create_listing(other_seller, price_cents=5_000, shipping_cents=0)

    response = await checkout(buyer, [(a["id"], 2), (b["id"], 1)])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["total_cents"] == 2 * 10_000 + 1_500 + 5_000
    orders = {o["seller"]["display_name"]: o for o in body["orders"]}
    assert set(orders) == {"Seller", "Other seller"}
    assert orders["Seller"]["total_cents"] == 21_500
    assert orders["Seller"]["status"] == "awaiting_payment"
    # Buyers do not see the platform's commission.
    assert orders["Seller"]["platform_fee_cents"] is None

    listing_a = (await buyer.get(f"/api/listings/{a['id']}")).json()
    listing_b = (await buyer.get(f"/api/listings/{b['id']}")).json()
    assert listing_a["stock"] == 1
    assert (listing_b["stock"], listing_b["status"]) == (0, "sold_out")
    await other_seller.aclose()


async def test_idempotency_key_returns_the_same_checkout(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, stock=5)
    first = await checkout(buyer, [(item["id"], 1)], key="same-key-123")
    again = await checkout(buyer, [(item["id"], 1)], key="same-key-123")
    other = await checkout(buyer, [(item["id"], 2)], key="same-key-123")
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert other.status_code == 400
    assert other.json()["error"]["code"] == "idempotency_mismatch"
    # Stock was reserved once.
    assert (await buyer.get(f"/api/listings/{item['id']}")).json()["stock"] == 4


async def test_cannot_buy_own_listing_or_more_than_available(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, stock=1)
    own = await checkout(seller, [(item["id"], 1)])
    assert own.json()["error"]["code"] == "own_listing"
    too_many = await checkout(buyer, [(item["id"], 2)])
    assert too_many.status_code == 409
    assert too_many.json()["error"]["code"] == "out_of_stock"
    assert too_many.json()["error"]["available"] == 1


async def test_a_failed_line_releases_the_whole_checkout(seller: Any, buyer: Any) -> None:
    plenty = await create_listing(seller, stock=5)
    scarce = await create_listing(seller, stock=1)
    response = await checkout(buyer, [(plenty["id"], 2), (scarce["id"], 2)])
    assert response.status_code == 409
    assert (await buyer.get(f"/api/listings/{plenty['id']}")).json()["stock"] == 5


async def test_concurrent_buyers_never_oversell_the_last_unit(app: Any, seller: Any) -> None:
    item = await create_listing(seller, stock=1)
    buyers = [await signed_in_client(app, f"race-{i}") for i in range(25)]

    results = await asyncio.gather(*(checkout(c, [(item["id"], 1)]) for c in buyers))
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1
    assert codes.count(409) == 24
    stock = await sql(app, "SELECT stock, status FROM listings WHERE id = :id", id=item["id"])
    assert stock == [(0, "sold_out")]
    for c in buyers:
        await c.aclose()


# --- paying ---------------------------------------------------------------------------------


async def test_pix_payment_is_confirmed_by_webhook_exactly_once(
    app: Any, seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller, price_cents=10_000, shipping_cents=2_000)
    await buyer.put(f"/api/cart/{item['id']}", json={"quantity": 1})
    created = (await checkout(buyer, [(item["id"], 1)])).json()

    started = (await pay(buyer, created["id"], "pix")).json()
    attempt = started["attempts"][0]
    assert attempt["status"] == "pending"
    assert attempt["client_data"]["pix_code"].startswith("PIX-")
    ref = next(iter(fake(app).payments))

    # Nothing happens until the provider says so.
    assert (await buyer.get(f"/api/checkouts/{created['id']}")).json()["status"] == "pending"

    fake(app).settle(ref)
    first = await webhook(app, ref, event_id="evt-1")
    replay = await webhook(app, ref, event_id="evt-1")
    assert first.json() == {"status": "received"}
    assert replay.json() == {"status": "duplicate"}

    paid = (await buyer.get(f"/api/checkouts/{created['id']}")).json()
    assert paid["status"] == "paid"
    assert paid["orders"][0]["status"] == "paid"
    assert (await buyer.get("/api/cart")).json()["items"] == []

    ledger = await sql(app, "SELECT kind::text, amount_cents FROM ledger_entries")
    assert sorted(ledger) == [("platform_fee", -1_000), ("sale", 12_000)]


async def test_webhooks_with_bad_signatures_are_rejected(app: Any) -> None:
    async with make_client(app) as anon:
        response = await anon.post(
            "/api/webhooks/fake",
            content=b'{"id": "x", "type": "t", "payment": "fake_1"}',
            headers={"x-fake-signature": "forged"},
        )
    assert response.status_code == 400


async def test_a_card_declined_then_approved_pays_once(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    card = {"payment_method_id": "master", "installments": 1, "payer_email": "b@example.com"}
    declined = (await pay(buyer, created["id"], "card", token="tok_declined", **card)).json()
    assert declined["status"] == "pending"
    assert declined["attempts"][0]["status"] == "failed"
    approved = (await pay(buyer, created["id"], "card", token="tok_approved", **card)).json()
    assert approved["status"] == "paid"
    retry = await pay(buyer, created["id"], "card", token="tok_approved", **card)
    assert retry.status_code == 409


async def test_double_payment_is_refunded(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"], "pix")
    pix_ref = next(iter(fake(app).payments))
    card = {"payment_method_id": "master", "installments": 1, "payer_email": "b@example.com"}
    await pay(buyer, created["id"], "card", token="tok_approved", **card)

    # The buyer also paid the Pix code they had abandoned.
    fake(app).settle(pix_ref)
    await webhook(app, pix_ref)

    result = (await buyer.get(f"/api/checkouts/{created['id']}")).json()
    assert result["status"] == "paid"
    assert fake(app).refunds == [(pix_ref, result["total_cents"])]
    statuses = sorted(a["status"] for a in result["attempts"])
    assert statuses == ["refunded", "succeeded"]


async def test_reconciliation_catches_a_lost_webhook(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"], "pix")
    ref = next(iter(fake(app).payments))
    fake(app).settle(ref)  # paid at the provider, webhook never arrives
    await sql(app, "UPDATE payment_attempts SET created_at = now() - interval '5 minutes'")

    results = await tick(app)
    assert results["reconcile_payments"] == 1
    status = await sql(app, "SELECT status FROM checkouts WHERE id = :id", id=created["id"])
    assert status == [("paid",)]


# --- expiry ---------------------------------------------------------------------------------


async def test_unpaid_checkouts_expire_and_return_stock(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, stock=1)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"], "pix")
    ref = next(iter(fake(app).payments))
    await sql(app, "UPDATE checkouts SET expires_at = now() - interval '1 second'")

    assert (await tick(app))["expire_checkouts"] == 1
    result = (await buyer.get(f"/api/checkouts/{created['id']}")).json()
    assert result["status"] == "expired"
    assert result["orders"][0]["status"] == "cancelled"
    assert fake(app).payments[ref]["status"] == AttemptStatus.CANCELLED
    listing = (await buyer.get(f"/api/listings/{item['id']}")).json()
    assert (listing["stock"], listing["status"]) == (1, "active")
    # Paying a closed checkout is refused.
    assert (await pay(buyer, created["id"], "pix")).status_code == 409


async def test_payment_found_at_expiry_wins_over_expiring(
    app: Any, seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"], "pix")
    fake(app).settle(next(iter(fake(app).payments)))
    await sql(app, "UPDATE checkouts SET expires_at = now() - interval '1 second'")
    await tick(app)
    assert (await buyer.get(f"/api/checkouts/{created['id']}")).json()["status"] == "paid"


async def test_expiry_waits_for_a_payment_under_review(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, stock=1)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"], "pix")
    ref = next(iter(fake(app).payments))
    fake(app).uncancellable.add(ref)
    await sql(app, "UPDATE checkouts SET expires_at = now() - interval '1 minute'")

    await tick(app)
    status = await sql(app, "SELECT status FROM checkouts WHERE id = :id", id=created["id"])
    assert status == [("pending",)]  # stock still held

    fake(app).settle(ref)
    await tick(app)
    status = await sql(app, "SELECT status FROM checkouts WHERE id = :id", id=created["id"])
    assert status == [("paid",)]
    assert fake(app).refunds == []


async def test_expiry_gives_up_after_the_grace_period(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, stock=1)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"], "pix")
    fake(app).uncancellable.add(next(iter(fake(app).payments)))
    await sql(app, "UPDATE checkouts SET expires_at = now() - interval '11 minutes'")
    await tick(app)
    status = await sql(app, "SELECT status FROM checkouts WHERE id = :id", id=created["id"])
    assert status == [("expired",)]
    assert (await buyer.get(f"/api/listings/{item['id']}")).json()["stock"] == 1


async def test_a_payment_after_expiry_is_refunded(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    created = (await checkout(buyer, [(item["id"], 1)])).json()
    await pay(buyer, created["id"], "pix")
    ref = next(iter(fake(app).payments))
    await buyer.post(f"/api/checkouts/{created['id']}/cancel")
    # The provider accepted the payment anyway (it raced the cancellation).
    fake(app).settle(ref)
    await webhook(app, ref)
    assert fake(app).refunds == [(ref, created["total_cents"])]
    status = await sql(app, "SELECT status FROM checkouts WHERE id = :id", id=created["id"])
    assert status == [("cancelled",)]


# --- after payment --------------------------------------------------------------------------


async def test_order_lifecycle_review_and_payout(app: Any, seller: Any, buyer: Any) -> None:
    connect = await seller.post("/api/me/payout-account", json={"provider": "fake"})
    assert connect.status_code == 200
    assert connect.json()["onboarding_url"].startswith("https://")

    data = await paid_checkout(app, seller, buyer, price_cents=20_000, shipping_cents=1_000)
    order_id = data["checkout"]["orders"][0]["id"]

    early_review = await buyer.post(f"/api/orders/{order_id}/review", json={"rating": 5})
    assert early_review.json()["error"]["code"] == "not_delivered"
    assert (
        await buyer.post(f"/api/orders/{order_id}/ship", json={"tracking_code": "BR123"})
    ).status_code == 403
    early_delivery = await buyer.post(f"/api/orders/{order_id}/deliver")
    assert early_delivery.json()["error"]["code"] == "invalid_transition"

    shipped = await seller.post(f"/api/orders/{order_id}/ship", json={"tracking_code": "BR123456"})
    assert shipped.json()["status"] == "shipped"
    assert shipped.json()["seller_net_cents"] == 21_000 - 2_000

    summary = (await seller.get("/api/me/sales/summary")).json()
    assert summary["in_escrow_cents"] == 19_000
    assert summary["to_receive_cents"] == 0

    delivered = await buyer.post(f"/api/orders/{order_id}/deliver")
    assert delivered.json()["status"] == "delivered"
    events = [e["to_status"] for e in delivered.json()["events"]]
    assert events == ["awaiting_payment", "paid", "shipped", "delivered"]

    assert (await tick(app))["seller_payouts"] == 1
    assert fake(app).transfers[0]["amount"] == 19_000
    assert (await tick(app))["seller_payouts"] == 0  # never paid twice
    summary = (await seller.get("/api/me/sales/summary")).json()
    assert summary == {
        "gross_cents": 21_000,
        "fees_cents": 2_000,
        "refunded_cents": 0,
        "paid_out_cents": 19_000,
        "in_escrow_cents": 0,
        "to_receive_cents": 0,
        "payout_account": True,
    }

    review = await buyer.post(
        f"/api/orders/{order_id}/review", json={"rating": 4, "comment": "  Works as described. "}
    )
    assert review.status_code == 201
    assert review.json()["comment"] == "Works as described."
    again = await buyer.post(f"/api/orders/{order_id}/review", json={"rating": 1})
    assert again.status_code == 409
    listing = (await buyer.get(f"/api/listings/{data['listing']['id']}")).json()
    assert (listing["seller"]["rating_avg"], listing["seller"]["rating_count"]) == (4.0, 1)
    profile = (await buyer.get(f"/api/users/{listing['seller']['id']}/reviews")).json()
    assert profile["reviews"][0]["items"] == [data["listing"]["title"]]


async def test_seller_cancels_a_paid_order_and_the_buyer_is_refunded(
    app: Any, seller: Any, buyer: Any
) -> None:
    data = await paid_checkout(app, seller, buyer, price_cents=10_000, shipping_cents=0, stock=1)
    order = data["checkout"]["orders"][0]
    response = await seller.post(f"/api/orders/{order['id']}/refund")
    assert response.status_code == 200
    assert response.json()["status"] == "refunded"
    assert fake(app).refunds[-1][1] == 10_000
    listing = (await buyer.get(f"/api/listings/{data['listing']['id']}")).json()
    assert (listing["stock"], listing["status"]) == (1, "active")
    summary = (await seller.get("/api/me/sales/summary")).json()
    assert (summary["gross_cents"], summary["refunded_cents"], summary["fees_cents"]) == (
        10_000,
        10_000,
        0,
    )
    # A refunded order cannot be shipped.
    ship = await seller.post(f"/api/orders/{order['id']}/ship", json={"tracking_code": "BR123"})
    assert ship.status_code == 409


async def test_orders_are_private_to_buyer_and_seller(app: Any, seller: Any, buyer: Any) -> None:
    data = await paid_checkout(app, seller, buyer)
    order_id = data["checkout"]["orders"][0]["id"]
    async with make_client(app) as stranger:
        await register(stranger)
        assert (await stranger.get(f"/api/orders/{order_id}")).status_code == 404
        assert (await stranger.get(f"/api/checkouts/{data['checkout']['id']}")).status_code == 404
        ship = await stranger.post(f"/api/orders/{order_id}/ship", json={"tracking_code": "BR123"})
        assert ship.status_code == 404
    selling = (await seller.get("/api/orders", params={"role": "seller"})).json()
    buying = (await buyer.get("/api/orders")).json()
    assert [o["id"] for o in selling] == [o["id"] for o in buying] == [order_id]
    assert selling[0]["platform_fee_cents"] is not None
    assert buying[0]["platform_fee_cents"] is None


async def test_checkout_from_cart(app: Any, seller: Any, buyer: Any) -> None:
    a = await create_listing(seller, price_cents=1_000, shipping_cents=0, stock=5)
    b = await create_listing(seller, price_cents=2_000, shipping_cents=500, stock=5)
    await buyer.put(f"/api/cart/{a['id']}", json={"quantity": 2})
    await buyer.put(f"/api/cart/{b['id']}", json={"quantity": 1})
    cart = (await buyer.get("/api/cart")).json()
    assert cart["total_cents"] == 2 * 1_000 + 2_000 + 500
    own = await seller.put(f"/api/cart/{a['id']}", json={"quantity": 1})
    assert own.json()["error"]["code"] == "own_listing"

    response = await buyer.post(
        "/api/checkouts",
        json={"provider": "fake", "from_cart": True},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201
    assert response.json()["total_cents"] == cart["total_cents"]
    assert len(response.json()["orders"]) == 1
