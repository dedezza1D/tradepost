import csv
import io
import uuid
from datetime import date
from typing import Any

from tests.conftest import create_listing, make_client
from tests.test_checkout import sql
from tests.test_commerce import buy, coupon, pay, sold
from tests.test_ops import admin_client
from tradepost.analytics import ViewCounter, flush_views, today, views


async def flush(app: Any) -> None:
    async with app.state.db.sessionmaker() as db, db.begin():
        await flush_views(db)


async def me(client: Any) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/auth/me")).json()["id"])


async def analytics(client: Any, days: int = 30) -> dict[str, Any]:
    response = await client.get("/api/me/analytics", params={"days": days})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def row(data: dict[str, Any], listing_id: str) -> dict[str, Any]:
    return next(r for r in data["listings"] if r["listing_id"] == listing_id)


def test_a_visitor_counts_once_a_day() -> None:
    counter = ViewCounter()
    listing = uuid.uuid4()
    assert counter.record(listing, "u:1")
    assert not counter.record(listing, "u:1")
    assert counter.record(listing, "u:2")
    assert counter.record(listing, "u:1", day=date(2020, 1, 1))  # another day
    assert counter.drain() == {(today(), listing): 2, (date(2020, 1, 1), listing): 1}
    assert counter.drain() == {}
    # The day has not turned, so today's visitors are still remembered.
    assert not counter.record(listing, "u:1")


async def test_views_leave_out_the_seller_admins_and_robots(
    app: Any, seller: Any, buyer: Any, client: Any
) -> None:
    views.drain()
    item = await create_listing(seller)
    url = f"/api/listings/{item['id']}"
    admin = await admin_client(app)
    for viewer in (buyer, buyer, client, seller, admin):
        assert (await viewer.get(url)).status_code == 200
    async with make_client(app) as same_browser:  # same address and browser as `client`
        await same_browser.get(url)
    async with make_client(app) as crawler:
        await crawler.get(url, headers={"user-agent": "WhatsApp/2.23 (link preview)"})
    await flush(app)
    await buyer.get(url)  # already counted today, even after the flush
    await flush(app)

    data = await analytics(seller)
    assert row(data, item["id"])["views"] == 2  # the buyer and one anonymous visitor
    assert data["totals"]["views"] == 2
    assert data["series"][-1]["views"] == 2
    assert await sql(app, "SELECT views FROM listing_daily_views") == [(2,)]


async def test_the_numbers_match_saves_questions_chats_and_orders(
    app: Any, seller: Any, buyer: Any
) -> None:
    views.drain()
    seller_id = await me(seller)
    item = await create_listing(seller, title="Bicicleta aro 29", price_cents=100000)
    other = await create_listing(seller, title="Capacete", price_cents=20000)

    await buyer.get(f"/api/listings/{item['id']}")
    await flush(app)
    assert (await buyer.put(f"/api/listings/{item['id']}/save")).status_code in (200, 204)
    asked = await buyer.post(
        f"/api/listings/{item['id']}/questions", json={"body": "Qual o tamanho do quadro?"}
    )
    assert asked.status_code == 201, asked.text

    # A conversation the seller answers 30 minutes later, and one still waiting.
    chat = (
        await buyer.post(
            "/api/conversations", json={"listing_id": item["id"], "body": "Ainda disponível?"}
        )
    ).json()
    assert (
        await seller.post(f"/api/conversations/{chat['id']}/messages", json={"body": "Sim!"})
    ).status_code == 201
    await sql(
        app,
        "UPDATE messages SET created_at = created_at + interval '30 minutes'"
        " WHERE conversation_id = :c AND sender_id = :s",
        c=uuid.UUID(chat["id"]),
        s=seller_id,
    )
    await buyer.post("/api/conversations", json={"listing_id": other["id"], "body": "Tem azul?"})

    # Two paid orders, one of them with a coupon; one checkout left unpaid.
    await sold(seller, buyer, shipped=False, title="Bomba de ar", price_cents=5000)
    await coupon(seller, code="BIKE10")
    with_coupon = (await buy(buyer, item["id"], coupon_code="BIKE10")).json()
    assert (await pay(buyer, with_coupon["id"])).json()["status"] == "paid"
    await buy(buyer, other["id"])

    data = await analytics(seller)
    totals = data["totals"]
    assert totals["saves"] == 1
    assert totals["questions"] == 1
    assert totals["conversations"] == 2
    assert totals["orders"] == 2
    paid = await sql(
        app,
        "SELECT sum(items_cents - discount_cents) FROM orders"
        " WHERE seller_id = :s AND status IN ('paid', 'shipped', 'delivered')",
        s=seller_id,
    )
    assert totals["revenue_cents"] == paid[0][0] == 5000 + 90000
    assert totals["conversion"] == 2.0  # two orders over one counted view

    bike = row(data, item["id"])
    assert (bike["views"], bike["saves"], bike["questions"], bike["conversations"]) == (1, 1, 1, 1)
    assert (bike["orders"], bike["units"], bike["revenue_cents"]) == (1, 1, 100000)
    assert bike["conversion"] == 1.0
    helmet = row(data, other["id"])
    assert (helmet["orders"], helmet["conversations"], helmet["conversion"]) == (0, 1, None)
    assert data["listings"][0]["listing_id"] == item["id"]  # best seller first

    day = data["series"][-1]
    assert day["date"] == today().isoformat()
    assert (day["orders"], day["revenue_cents"], day["conversations"]) == (2, 95000, 2)
    assert len(data["series"]) == 30

    assert data["response"] == {"conversations": 2, "answered": 1, "median_minutes": 30.0}
    assert data["coupons"] == {
        "orders_with_coupon": 1,
        "discount_cents": 10000,
        "revenue_with_coupon_cents": 90000,
        "revenue_without_coupon_cents": 5000,
        "by_code": [
            {"code": "BIKE10", "orders": 1, "discount_cents": 10000, "revenue_cents": 90000}
        ],
    }

    # The buyer's own dashboard is empty: none of this is theirs.
    mine = await analytics(buyer)
    assert mine["totals"]["orders"] == 0
    assert mine["listings"] == []


async def test_the_period_leaves_out_older_activity(app: Any, seller: Any, buyer: Any) -> None:
    order = await sold(seller, buyer, title="Luminária", price_cents=8000)
    await sql(
        app,
        "UPDATE orders SET paid_at = paid_at - interval '10 days' WHERE id = :id",
        id=uuid.UUID(order["id"]),
    )
    assert (await analytics(seller, days=7))["totals"]["orders"] == 0
    assert (await analytics(seller, days=30))["totals"]["orders"] == 1
    assert (await seller.get("/api/me/analytics", params={"days": 0})).status_code == 422


async def test_the_export_has_a_row_per_listing_and_day(
    app: Any, seller: Any, buyer: Any, client: Any
) -> None:
    views.drain()
    item = await create_listing(seller, title="Cadeira de balanço", price_cents=30000)
    await client.get(f"/api/listings/{item['id']}")
    await flush(app)
    await sold(seller, buyer, title="Mesa de centro", price_cents=45000)

    response = await seller.get("/api/me/analytics/export.csv", params={"days": 7})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8-sig")
    lines = list(csv.DictReader(io.StringIO(text)))
    by_title = {line["title"]: line for line in lines}
    assert set(by_title) == {"Cadeira de balanço", "Mesa de centro"}
    assert by_title["Cadeira de balanço"]["views"] == "1"
    assert by_title["Mesa de centro"]["orders"] == "1"
    assert by_title["Mesa de centro"]["revenue_brl"] == "450.00"
    assert all(line["date"] == today().isoformat() for line in lines)
    assert (await client.get("/api/me/analytics/export.csv")).status_code == 401
