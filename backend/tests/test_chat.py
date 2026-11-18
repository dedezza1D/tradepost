"""Conversations, messages, offers and paying an accepted offer."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from tests.conftest import create_listing, make_client, register
from tradepost.chat.detection import CONTACT, OFF_PLATFORM_PAYMENT, warning_for
from tradepost.chat.service import expire_due

CARD = {
    "token": "tok_approved",
    "payment_method_id": "visa",
    "installments": 1,
    "payer_email": "b@example.com",
}


async def start(client: Any, listing_id: str, body: str | None = None) -> dict[str, Any]:
    response = await client.post(
        "/api/conversations", json={"listing_id": listing_id, **({"body": body} if body else {})}
    )
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


async def say(client: Any, conversation_id: str, body: str) -> Any:
    return await client.post(f"/api/conversations/{conversation_id}/messages", json={"body": body})


async def history(client: Any, conversation_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


async def offer(client: Any, conversation_id: str, cents: int) -> Any:
    return await client.post(
        f"/api/conversations/{conversation_id}/offers", json={"amount_cents": cents}
    )


async def notification_kinds(client: Any) -> list[str]:
    return [n["kind"] for n in (await client.get("/api/notifications")).json()["items"]]


async def sql(app: Any, statement: str, **params: Any) -> None:
    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(text(statement), params)


# --- conversations and messages --------------------------------------------------------------


async def test_only_the_two_participants_can_read_or_write(
    app: Any, seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"], "Ainda está disponível?")
    assert conversation["role"] == "buyer"
    assert (await start(buyer, item["id"]))["id"] == conversation["id"]

    reply = await say(seller, conversation["id"], "Sim, está!")
    assert reply.status_code == 201
    assert [m["body"] for m in await history(buyer, conversation["id"])] == [
        "Ainda está disponível?",
        "Sim, está!",
    ]

    async with make_client(app) as stranger:
        await register(stranger, display_name="Stranger")
        cid = conversation["id"]
        assert (await stranger.get(f"/api/conversations/{cid}")).status_code == 404
        assert (await stranger.get(f"/api/conversations/{cid}/messages")).status_code == 404
        assert (await say(stranger, cid, "oi")).status_code == 404
        assert (await stranger.get("/api/conversations")).json()["items"] == []

    own = await seller.post("/api/conversations", json={"listing_id": item["id"]})
    assert own.json()["error"]["code"] == "own_listing_chat"


async def test_unread_counts_seen_marks_and_one_notification_per_conversation(
    seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"], "Oi!")
    for text_ in ("Aceita troca?", "Posso buscar hoje?"):
        await say(buyer, conversation["id"], text_)

    inbox = (await seller.get("/api/conversations")).json()
    assert inbox["unread_total"] == 1
    assert inbox["items"][0]["unread"] == 3
    assert inbox["items"][0]["role"] == "seller"
    assert inbox["items"][0]["last_message_preview"] == "Posso buscar hoje?"
    assert (await notification_kinds(seller)).count("new_message") == 1

    assert (await buyer.get(f"/api/conversations/{conversation['id']}")).json()[
        "other_read_at"
    ] is None
    assert (await seller.post(f"/api/conversations/{conversation['id']}/read")).status_code == 204
    assert (await seller.get("/api/conversations/unread")).json() == {"count": 0}
    # Reading clears the chat notification and shows the buyer a "seen" mark.
    assert (await seller.get("/api/notifications")).json()["unread"] == 0
    seen = (await buyer.get(f"/api/conversations/{conversation['id']}")).json()["other_read_at"]
    assert seen is not None
    # Your own messages are never unread for you.
    assert (await buyer.get("/api/conversations")).json()["items"][0]["unread"] == 0


async def test_messages_are_paginated_newest_first_page(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"])
    for n in range(5):
        await say(buyer, conversation["id"], f"mensagem {n}")
    url = f"/api/conversations/{conversation['id']}/messages"
    newest = (await buyer.get(url, params={"limit": 2})).json()
    assert [m["body"] for m in newest["items"]] == ["mensagem 3", "mensagem 4"]
    assert newest["has_older"] is True
    older = (await buyer.get(url, params={"limit": 2, "before": newest["items"][0]["id"]})).json()
    assert [m["body"] for m in older["items"]] == ["mensagem 1", "mensagem 2"]
    since = (await buyer.get(url, params={"after": newest["items"][0]["id"]})).json()
    assert [m["body"] for m in since["items"]] == ["mensagem 4"]


async def test_messages_have_a_size_limit(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"])
    assert (await say(buyer, conversation["id"], "x" * 1001)).status_code == 422
    assert (await say(buyer, conversation["id"], "   ")).status_code == 422


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Ainda está disponível?", None),
        ("Faço por R$ 1.250,00 à vista", None),
        ("me chama no (81) 99999-1234", CONTACT),
        ("meu zap 81 9 9999 1234", CONTACT),
        ("manda pro email ana.vendas@example.com", CONTACT),
        ("faz um pix que eu envio hoje", OFF_PLATFORM_PAYMENT),
        ("paga por esse link https://pay.example/abc", OFF_PLATFORM_PAYMENT),
        ("prefiro transferência", OFF_PLATFORM_PAYMENT),
    ],
)
def test_off_platform_detection(message: str, expected: str | None) -> None:
    assert warning_for(message) == expected


async def test_flagged_messages_are_delivered_with_a_warning(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"])
    sent = (await say(seller, conversation["id"], "Me paga no pix 81999991234")).json()
    assert sent["warning"] == OFF_PLATFORM_PAYMENT
    assert (await history(buyer, conversation["id"]))[0]["warning"] == OFF_PLATFORM_PAYMENT


async def test_blocking_stops_new_messages_but_keeps_history(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"], "Oi")
    buyer_id = (await buyer.get("/api/auth/me")).json()["id"]
    await seller.put(f"/api/users/{buyer_id}/block")
    blocked = await say(buyer, conversation["id"], "Oi de novo")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "blocked"
    assert (await offer(buyer, conversation["id"], 100000)).status_code == 403
    assert len(await history(buyer, conversation["id"])) == 1
    assert (await buyer.get(f"/api/conversations/{conversation['id']}")).json()["blocked"] is True


# --- offers ----------------------------------------------------------------------------------


async def test_offer_counter_accept_and_pay_the_agreed_price(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, price_cents=120000, shipping_cents=5000)
    conversation = await start(buyer, item["id"])
    first = (await offer(buyer, conversation["id"], 90000)).json()
    assert first["status"] == "pending"
    assert "offer_received" in await notification_kinds(seller)

    countered = await seller.post(
        f"/api/offers/{first['id']}/counter", json={"amount_cents": 105000}
    )
    assert countered.status_code == 200, countered.text
    second = countered.json()
    assert "offer_countered" in await notification_kinds(buyer)

    accepted = (await buyer.post(f"/api/offers/{second['id']}/accept")).json()
    assert accepted["status"] == "accepted"
    assert accepted["pay_by"] is not None
    assert "offer_accepted" in await notification_kinds(seller)
    kinds = [m["kind"] for m in await history(seller, conversation["id"])]
    assert kinds == ["offer", "offer", "offer_accepted"]
    assert [m["offer"]["status"] for m in await history(seller, conversation["id"])][:2] == [
        "countered",
        "accepted",
    ]

    # The seller cannot pay the buyer's deal.
    paid_by_seller = await seller.post(
        f"/api/offers/{second['id']}/checkout",
        json={"provider": "fake"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert paid_by_seller.status_code == 403

    response = await buyer.post(
        f"/api/offers/{second['id']}/checkout",
        json={"provider": "fake", "items": [{"listing_id": item["id"], "quantity": 3}]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text
    checkout = response.json()
    line = checkout["orders"][0]["items"][0]
    assert line["unit_price_cents"] == 105000  # the agreed price, not the listed 120000
    assert line["quantity"] == 1  # extra fields in the body are ignored
    assert checkout["total_cents"] == 105000 + 5000

    again = await buyer.post(
        f"/api/offers/{second['id']}/checkout",
        json={"provider": "fake"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert again.status_code == 200
    assert again.json()["id"] == checkout["id"]

    paid = await buyer.post(
        f"/api/checkouts/{checkout['id']}/payments", json={"method": "card", "card": CARD}
    )
    assert paid.json()["status"] == "paid"
    used = await buyer.post(
        f"/api/offers/{second['id']}/checkout",
        json={"provider": "fake"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert used.json()["error"]["code"] == "offer_used"
    assert (await history(buyer, conversation["id"]))[-1]["offer"]["paid"] is True

    # Buying the listing directly still charges the listed price.
    direct = await buyer.post(
        "/api/checkouts",
        json={"provider": "fake", "items": [{"listing_id": item["id"], "quantity": 1}]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert direct.status_code == 409  # stock was 1 and is sold


async def test_an_offer_is_accepted_only_once_under_concurrency(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"])
    pending = (await offer(buyer, conversation["id"], 100000)).json()
    results = await asyncio.gather(
        seller.post(f"/api/offers/{pending['id']}/accept"),
        seller.post(f"/api/offers/{pending['id']}/accept"),
        seller.post(f"/api/offers/{pending['id']}/decline"),
    )
    assert sorted(r.status_code for r in results) == [200, 409, 409]
    events = [m["kind"] for m in await history(buyer, conversation["id"])]
    assert len([k for k in events if k in ("offer_accepted", "offer_declined")]) == 1


async def test_offer_rules(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, price_cents=120000)
    conversation = await start(buyer, item["id"])
    cid = conversation["id"]
    assert (await offer(seller, cid, 100000)).json()["error"]["code"] == "buyer_only"
    assert (await offer(buyer, cid, 130000)).json()["error"]["code"] == "offer_above_price"
    pending = (await offer(buyer, cid, 100000)).json()
    assert (await offer(buyer, cid, 110000)).json()["error"]["code"] == "offer_pending"
    own = await buyer.post(f"/api/offers/{pending['id']}/accept")
    assert own.json()["error"]["code"] == "not_your_turn"
    assert (await seller.post(f"/api/offers/{pending['id']}/cancel")).status_code == 403

    cancelled = await buyer.post(f"/api/offers/{pending['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    late = await seller.post(f"/api/offers/{pending['id']}/accept")
    assert late.json()["error"]["code"] == "offer_closed"
    # A new offer is possible once the previous one is closed.
    assert (await offer(buyer, cid, 110000)).status_code == 201


async def test_offers_expire_after_a_day(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"])
    pending = (await offer(buyer, conversation["id"], 100000)).json()
    await sql(
        app,
        "UPDATE offers SET expires_at = :t WHERE id = :id",
        t=datetime.now(UTC) - timedelta(minutes=1),
        id=pending["id"],
    )
    # Even before the job runs, an expired offer cannot be accepted.
    early = await seller.post(f"/api/offers/{pending['id']}/accept")
    assert early.json()["error"]["code"] == "offer_expired"

    async with app.state.db.sessionmaker() as db, db.begin():
        assert await expire_due(db) == 1
    last = (await history(buyer, conversation["id"]))[-1]
    assert last["kind"] == "offer_expired"
    assert last["offer"]["status"] == "expired"
    assert "offer_expired" in await notification_kinds(buyer)


async def test_an_accepted_offer_must_be_paid_within_a_day(
    app: Any, seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller)
    conversation = await start(buyer, item["id"])
    pending = (await offer(buyer, conversation["id"], 100000)).json()
    await seller.post(f"/api/offers/{pending['id']}/accept")
    await sql(
        app,
        "UPDATE offers SET responded_at = :t WHERE id = :id",
        t=datetime.now(UTC) - timedelta(hours=25),
        id=pending["id"],
    )
    late = await buyer.post(
        f"/api/offers/{pending['id']}/checkout",
        json={"provider": "fake"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert late.json()["error"]["code"] == "offer_expired"
