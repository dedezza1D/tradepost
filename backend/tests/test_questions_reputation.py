"""Public questions on listings and seller reputation."""

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from tests.conftest import create_listing, make_client, register
from tradepost.reviews.reputation import level_for

CARD = {
    "token": "tok_approved",
    "payment_method_id": "visa",
    "installments": 1,
    "payer_email": "b@example.com",
}


async def kinds(client: Any) -> list[str]:
    return [n["kind"] for n in (await client.get("/api/notifications")).json()["items"]]


# --- questions -------------------------------------------------------------------------------


async def test_questions_are_answered_by_the_seller_and_shown_publicly(
    app: Any, seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller)
    url = f"/api/listings/{item['id']}/questions"
    asked = await buyer.post(url, json={"body": "Aceita retirar no centro?"})
    assert asked.status_code == 201, asked.text
    qid = asked.json()["id"]
    assert "question_received" in await kinds(seller)

    async with make_client(app) as anon:
        assert (await anon.get(url)).json()["items"] == []  # unanswered stays private
    assert [q["id"] for q in (await buyer.get(url)).json()["items"]] == [qid]
    seller_view = (await seller.get(url)).json()
    assert seller_view["unanswered"] == 1

    answered = await seller.post(f"/api/questions/{qid}/answer", json={"body": "Sim, no centro."})
    assert answered.status_code == 200
    assert "question_answered" in await kinds(buyer)
    async with make_client(app) as anon:
        public = (await anon.get(url)).json()["items"]
    assert public[0]["answer"] == "Sim, no centro."
    assert public[0]["asker_name"] == "Buyer"  # first name only
    assert (await seller.get(url)).json()["unanswered"] == 0


async def test_question_rules(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    url = f"/api/listings/{item['id']}/questions"
    own = await seller.post(url, json={"body": "Pergunta minha?"})
    assert own.json()["error"]["code"] == "own_listing_question"
    phone = await buyer.post(url, json={"body": "Me chama no 81 99999-1234"})
    assert phone.json()["error"]["code"] == "contact_not_allowed"

    qid = (await buyer.post(url, json={"body": "Tem nota fiscal?"})).json()["id"]
    async with make_client(app) as other:
        await register(other, display_name="Other")
        answer = await other.post(f"/api/questions/{qid}/answer", json={"body": "Tem"})
        assert answer.status_code == 403
        assert (await other.delete(f"/api/questions/{qid}")).status_code == 403
    pix = await seller.post(
        f"/api/questions/{qid}/answer", json={"body": "Faz um pix que eu mando"}
    )
    assert pix.json()["error"]["code"] == "contact_not_allowed"

    # The asker withdraws an unanswered question; the seller hides answered ones.
    assert (await buyer.delete(f"/api/questions/{qid}")).status_code == 204
    assert (await seller.get(url)).json()["items"] == []
    second = (await buyer.post(url, json={"body": "Ainda tem?"})).json()["id"]
    await seller.post(f"/api/questions/{second}/answer", json={"body": "Tem sim"})
    assert (await buyer.delete(f"/api/questions/{second}")).status_code == 403
    assert (await seller.delete(f"/api/questions/{second}")).status_code == 204


# --- reputation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("completed", "cancel", "late", "rating", "level"),
    [
        (2, 0.0, 0.0, 5.0, "new"),
        (3, 0.0, 0.0, None, "good"),
        (12, 0.0, 0.0, 4.8, "top"),
        (12, 0.0, 0.1, 4.8, "good"),
        (12, 0.04, 0.0, 4.2, "good"),
        (12, 0.2, 0.0, 4.8, "attention"),
        (5, 0.0, 0.0, 3.5, "attention"),
    ],
)
def test_reputation_levels(
    completed: int, cancel: float, late: float, rating: float | None, level: str
) -> None:
    assert level_for(completed, cancel, late, rating) == level


async def sell_and_deliver(seller: Any, buyer: Any, listing_id: str) -> str:
    checkout = (
        await buyer.post(
            "/api/checkouts",
            json={"provider": "fake", "items": [{"listing_id": listing_id, "quantity": 1}]},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
    ).json()
    await buyer.post(
        f"/api/checkouts/{checkout['id']}/payments", json={"method": "card", "card": CARD}
    )
    order_id: str = checkout["orders"][0]["id"]
    await seller.post(f"/api/orders/{order_id}/ship", json={"tracking_code": "BR123456789"})
    await buyer.post(f"/api/orders/{order_id}/deliver")
    return order_id


async def test_reputation_comes_from_recorded_sales(app: Any, seller: Any, buyer: Any) -> None:
    seller_id = (await seller.get("/api/auth/me")).json()["id"]
    item = await create_listing(seller, stock=5)
    profile = (await buyer.get(f"/api/users/{seller_id}")).json()
    assert profile["reputation"]["level"] == "new"

    orders = [await sell_and_deliver(seller, buyer, item["id"]) for _ in range(3)]
    profile = (await buyer.get(f"/api/users/{seller_id}")).json()
    assert profile["reputation"]["completed_sales"] == 3
    assert profile["reputation"]["level"] == "good"
    assert (await buyer.get(f"/api/listings/{item['id']}")).json()["seller"]["reputation"] == "good"

    # Two of three shipped late: the level drops.
    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(
            text("UPDATE orders SET shipped_at = paid_at + interval '5 days' WHERE id = ANY(:ids)"),
            {"ids": [uuid.UUID(o) for o in orders[:2]]},
        )
    profile = (await buyer.get(f"/api/users/{seller_id}")).json()
    assert profile["reputation"]["late_rate"] == pytest.approx(0.667, abs=0.001)
    assert profile["reputation"]["level"] == "attention"
