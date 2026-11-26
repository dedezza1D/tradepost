"""Card installments, seller coupons, disputes and the payouts they hold back."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.conftest import create_listing, make_client, register
from tests.test_checkout import fake, signed_in_client, sql, tick
from tradepost.config import get_settings
from tradepost.disputes import escalate_overdue
from tradepost.orders.processing import run_payouts


def card(installments: int = 1) -> dict[str, Any]:
    return {
        "token": "tok_approved",
        "payment_method_id": "visa",
        "installments": installments,
        "payer_email": "b@example.com",
    }


async def buy(buyer: Any, listing_id: str, **extra: Any) -> Any:
    return await buyer.post(
        "/api/checkouts",
        json={"provider": "fake", "items": [{"listing_id": listing_id, "quantity": 1}], **extra},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )


async def pay(buyer: Any, checkout_id: str, installments: int = 1) -> Any:
    return await buyer.post(
        f"/api/checkouts/{checkout_id}/payments",
        json={"method": "card", "card": card(installments)},
    )


async def sold(seller: Any, buyer: Any, *, shipped: bool = True, **listing: Any) -> dict[str, Any]:
    """A paid order (shipped by default), as the buyer sees it."""
    item = await create_listing(seller, **listing)
    created = (await buy(buyer, item["id"])).json()
    assert (await pay(buyer, created["id"])).json()["status"] == "paid"
    order: dict[str, Any] = created["orders"][0]
    if shipped:
        assert (
            await seller.post(
                f"/api/orders/{order['id']}/ship", json={"tracking_code": "BR123456789"}
            )
        ).status_code == 200
    return order


# --- installments ----------------------------------------------------------------------------


async def test_installments_are_recorded_and_capped(
    app: Any, seller: Any, buyer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers = (await buyer.get("/api/payments/providers")).json()
    assert providers[0]["config"]["max_installments"] == 12

    item = await create_listing(seller, stock=2)
    created = (await buy(buyer, item["id"])).json()
    paid = await pay(buyer, created["id"], installments=6)
    assert paid.json()["status"] == "paid"
    assert paid.json()["attempts"][-1]["status"] == "succeeded"
    order = (await buyer.get("/api/orders", params={"role": "buyer"})).json()[0]
    assert order["installments"] == 6
    assert fake(app).payments[list(fake(app).payments)[-1]]["amount"] == order["total_cents"]

    monkeypatch.setattr(get_settings(), "max_installments", 3)
    second = (await buy(buyer, item["id"])).json()
    too_many = await pay(buyer, second["id"], installments=6)
    assert too_many.json()["error"]["code"] == "too_many_installments"


# --- coupons ---------------------------------------------------------------------------------


async def coupon(seller: Any, **body: Any) -> dict[str, Any]:
    response = await seller.post("/api/me/coupons", json={"kind": "percent", "value": 10, **body})
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def test_a_percentage_coupon_discounts_items_not_shipping(seller: Any, buyer: Any) -> None:
    await coupon(seller, code="bike10")
    item = await create_listing(seller, price_cents=100000, shipping_cents=5000, stock=3)
    lines = [{"listing_id": item["id"], "quantity": 1}]
    quote = (
        await buyer.post("/api/checkouts/quote", json={"items": lines, "coupon_code": "BIKE10"})
    ).json()
    assert quote["orders"][0]["discount_cents"] == 10000
    assert quote["total_cents"] == 100000 + 5000 - 10000

    created = await buy(buyer, item["id"], coupon_code="bike10")
    assert created.status_code == 201, created.text
    order = created.json()["orders"][0]
    assert order["total_cents"] == 95000
    assert order["discount_cents"] == 10000
    seller_view = (await seller.get("/api/orders", params={"role": "seller"})).json()[0]
    assert seller_view["platform_fee_cents"] == 9000  # 10% of 90000, after the discount
    assert (await seller.get("/api/me/coupons")).json()[0]["uses"] == 1

    # Once per buyer; cancelling the unpaid checkout gives the use back.
    again = await buy(buyer, item["id"], coupon_code="BIKE10")
    assert again.json()["error"]["code"] == "coupon_used"
    await buyer.post(f"/api/checkouts/{created.json()['id']}/cancel")
    assert (await seller.get("/api/me/coupons")).json()[0]["uses"] == 0
    assert (await buy(buyer, item["id"], coupon_code="BIKE10")).status_code == 201


async def test_coupon_limits(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, price_cents=10000, stock=5)
    await coupon(seller, code="BIG", min_items_cents=50000)
    await coupon(seller, code="OLD", expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    off = await coupon(seller, code="OFF")
    await seller.patch(f"/api/me/coupons/{off['id']}", json={"active": False})
    await coupon(seller, code="FIXED", kind="fixed", value=999999)
    async with make_client(app) as other:
        await register(other, display_name="Other seller")
        await coupon(other, code="THEIRS")

    async def code_error(code: str) -> str:
        response = await buy(buyer, item["id"], coupon_code=code)
        return str(response.json().get("error", {}).get("code"))

    assert await code_error("BIG") == "coupon_min"
    assert await code_error("OLD") == "coupon_invalid"
    assert await code_error("OFF") == "coupon_invalid"
    assert await code_error("THEIRS") == "coupon_invalid"  # another seller's code
    assert await code_error("NOPE") == "coupon_invalid"
    # A fixed coupon larger than the items takes the items to zero, never the shipping...
    fixed = (await buy(buyer, item["id"], coupon_code="FIXED")).json()
    assert fixed["total_cents"] == 5000
    # ...and with free shipping it still leaves the minimum a provider will charge.
    free = await create_listing(seller, price_cents=10000, shipping_cents=0)
    async with make_client(app) as second:
        await register(second, display_name="Second buyer")
        minimum = (await buy(second, free["id"], coupon_code="FIXED")).json()
    assert minimum["total_cents"] == 100


async def test_a_limited_coupon_is_not_overused(app: Any, seller: Any) -> None:
    await coupon(seller, code="ONLYONE", max_uses=1)
    item = await create_listing(seller, stock=5)
    buyers = [await signed_in_client(app, f"buyer{n}") for n in range(4)]
    try:
        results = await asyncio.gather(*(buy(b, item["id"], coupon_code="ONLYONE") for b in buyers))
    finally:
        for b in buyers:
            await b.aclose()
    assert sorted(r.status_code for r in results).count(201) == 1
    assert (await seller.get("/api/me/coupons")).json()[0]["uses"] == 1


# --- disputes --------------------------------------------------------------------------------


async def open_dispute(buyer: Any, order_id: str, reason: str = "not_as_described") -> Any:
    return await buyer.post(
        f"/api/orders/{order_id}/dispute", json={"reason": reason, "body": "Chegou com defeito."}
    )


async def test_a_dispute_opens_after_shipping_and_the_seller_refunds_part(
    app: Any, seller: Any, buyer: Any
) -> None:
    await seller.post("/api/me/payout-account", json={"provider": "fake"})
    order = await sold(seller, buyer, shipped=False, price_cents=100000, shipping_cents=0)
    early = await open_dispute(buyer, order["id"])
    assert early.json()["error"]["code"] == "dispute_window_closed"
    await seller.post(f"/api/orders/{order['id']}/ship", json={"tracking_code": "BR123456789"})

    dispute = (await open_dispute(buyer, order["id"])).json()
    assert dispute["status"] == "open"
    assert dispute["my_role"] == "buyer"
    assert (await open_dispute(buyer, order["id"])).json()["error"]["code"] == "dispute_exists"
    assert "dispute_opened" in [
        n["kind"] for n in (await seller.get("/api/notifications")).json()["items"]
    ]
    async with make_client(app) as stranger:
        await register(stranger, display_name="Stranger")
        assert (await stranger.get(f"/api/disputes/{dispute['id']}")).status_code == 404

    talk = await seller.post(
        f"/api/disputes/{dispute['id']}/messages", json={"body": "Pode mandar foto?"}
    )
    assert [m["author_role"] for m in talk.json()["messages"]] == ["buyer", "seller"]
    too_much = await seller.post(
        f"/api/disputes/{dispute['id']}/refund", json={"amount_cents": 200000}
    )
    assert too_much.json()["error"]["code"] == "refund_too_large"
    refunded = await seller.post(
        f"/api/disputes/{dispute['id']}/refund", json={"amount_cents": 30000}
    )
    assert refunded.json()["status"] == "refunded"
    assert fake(app).refunds[-1][1] == 30000

    # The order goes on; the seller's payout is what is left: 100000 - 10000 fee - 30000 + 3000.
    await buyer.post(f"/api/orders/{order['id']}/deliver")
    assert (await tick(app))["seller_payouts"] == 1
    assert fake(app).transfers[-1]["amount"] == 63000


async def test_a_full_refund_closes_the_order_and_nothing_is_paid_out(
    app: Any, seller: Any, buyer: Any
) -> None:
    await seller.post("/api/me/payout-account", json={"provider": "fake"})
    order = await sold(seller, buyer)
    await buyer.post(f"/api/orders/{order['id']}/deliver")
    dispute = (await open_dispute(buyer, order["id"], "damaged")).json()
    # Refunding twice at once refunds once.
    results = await asyncio.gather(
        seller.post(f"/api/disputes/{dispute['id']}/refund", json={}),
        seller.post(f"/api/disputes/{dispute['id']}/refund", json={}),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert len(fake(app).refunds) == 1
    status = (await buyer.get(f"/api/orders/{order['id']}")).json()["status"]
    assert status == "refunded"
    await tick(app)
    assert fake(app).transfers == []


async def test_the_platform_decides_after_the_seller_rejects(
    app: Any, seller: Any, buyer: Any
) -> None:
    order = await sold(seller, buyer)
    dispute = (await open_dispute(buyer, order["id"], "not_received")).json()
    early = await buyer.post(f"/api/disputes/{dispute['id']}/escalate")
    assert early.json()["error"]["code"] == "dispute_not_escalable"
    rejected = await seller.post(
        f"/api/disputes/{dispute['id']}/reject", json={"note": "Foi entregue."}
    )
    assert rejected.json()["status"] == "seller_rejected"
    assert (await buyer.get(f"/api/disputes/{dispute['id']}")).json()["can_escalate"] is True
    assert (await buyer.post(f"/api/disputes/{dispute['id']}/escalate")).json()[
        "status"
    ] == "escalated"
    # Once with the platform, the seller no longer decides.
    assert (await seller.post(f"/api/disputes/{dispute['id']}/refund", json={})).status_code == 409

    admin = await signed_in_client(app, "admin")
    try:
        assert (await admin.get("/api/admin/disputes")).status_code == 403
        admin_id = (await admin.get("/api/auth/me")).json()["id"]
        await sql(app, "UPDATE users SET is_admin = true WHERE id = :id", id=uuid.UUID(admin_id))
        # Administration needs a session signed in with the second factor (auth level 2).
        await sql(
            app, "UPDATE sessions SET auth_level = 2 WHERE user_id = :id", id=uuid.UUID(admin_id)
        )
        queue = (await admin.get("/api/admin/disputes")).json()
        assert [d["id"] for d in queue] == [dispute["id"]]
        decided = await admin.post(
            f"/api/disputes/{dispute['id']}/refund", json={"note": "Sem prova de entrega."}
        )
        assert decided.json()["status"] == "refunded"
        assert decided.json()["resolved_by"] == "platform"
    finally:
        await admin.aclose()
    assert (await buyer.get(f"/api/orders/{order['id']}")).json()["status"] == "refunded"


async def test_unanswered_disputes_go_to_the_platform(app: Any, seller: Any, buyer: Any) -> None:
    order = await sold(seller, buyer)
    dispute = (await open_dispute(buyer, order["id"])).json()
    await sql(
        app,
        "UPDATE disputes SET created_at = now() - interval '4 days' WHERE id = :id",
        id=uuid.UUID(dispute["id"]),
    )
    assert (await buyer.get(f"/api/disputes/{dispute['id']}")).json()["can_escalate"] is True
    async with app.state.db.sessionmaker() as db, db.begin():
        assert await escalate_overdue(db) == 1
    assert (await seller.get(f"/api/disputes/{dispute['id']}")).json()["status"] == "escalated"


async def test_payouts_wait_for_the_dispute_window_and_open_disputes(
    app: Any, seller: Any, buyer: Any
) -> None:
    await seller.post("/api/me/payout-account", json={"provider": "fake"})
    order = await sold(seller, buyer)
    await buyer.post(f"/api/orders/{order['id']}/deliver")

    async def payouts(hold: timedelta) -> int:
        async with app.state.db.sessionmaker() as db, db.begin():
            return await run_payouts(db, app.state.payments, hold)

    assert await payouts(timedelta(days=7)) == 0  # delivered just now
    dispute = (await open_dispute(buyer, order["id"])).json()
    assert await payouts(timedelta(0)) == 0  # held while the dispute is open
    await buyer.post(f"/api/disputes/{dispute['id']}/cancel")
    assert await payouts(timedelta(0)) == 1


async def test_disputes_close_seven_days_after_delivery(app: Any, seller: Any, buyer: Any) -> None:
    order = await sold(seller, buyer)
    await buyer.post(f"/api/orders/{order['id']}/deliver")
    await sql(
        app,
        "UPDATE orders SET delivered_at = now() - interval '8 days' WHERE id = :id",
        id=uuid.UUID(order["id"]),
    )
    late = await open_dispute(buyer, order["id"])
    assert late.json()["error"]["code"] == "dispute_window_closed"
