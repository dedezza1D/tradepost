import dataclasses
import json
import os
import uuid
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from tests.conftest import create_listing
from tests.test_chat import say, start
from tests.test_checkout import sql
from tests.test_commerce import sold
from tradepost.notifications import texts
from tradepost.notifications.delivery import send_due
from tradepost.notifications.email import LogEmail, NoEmail
from tradepost.notifications.webpush import (
    WebPush,
    b64url_decode,
    b64url_encode,
    encrypt,
    generate_vapid_private_key,
    private_key_from,
    vapid_authorization,
)

ENDPOINT = "https://fcm.googleapis.com/fcm/send/"


# --- the protocol ---------------------------------------------------------------------------


def test_encryption_matches_the_rfc_8291_example() -> None:
    expected = (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"
        "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT"
        "pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )
    body = encrypt(
        b"When I grow up, I want to be a watermelon",
        b64url_decode(
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
        ),
        b64url_decode("BTBZMqHH6r4Tts7J_aSIgg"),
        sender=private_key_from("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
        salt=b64url_decode(expected)[:16],
    )
    assert b64url_encode(body) == expected


def test_vapid_token_is_signed_for_the_push_service_origin() -> None:
    key = generate_vapid_private_key()
    header = vapid_authorization(ENDPOINT + "abc", key, "mailto:ops@example.com", now=1_000_000)
    token, public = header.removeprefix("vapid t=").split(", k=")
    head, claims, signature = token.split(".")
    assert json.loads(b64url_decode(claims)) == {
        "aud": "https://fcm.googleapis.com",
        "exp": 1_000_000 + 12 * 3600,
        "sub": "mailto:ops@example.com",
    }
    raw = b64url_decode(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    verifier = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64url_decode(public))
    verifier.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if bad


def test_texts_follow_the_readers_language() -> None:
    assert texts.money(123456, "pt-BR") == "R$ 1.234,56"
    assert texts.money(123456, "en") == "R$1,234.56"
    payload = {"title": "Bicicleta", "more_items": 2, "total_cents": 5000, "order_id": "o"}
    assert texts.sentence("order_paid", payload, "en") == (
        "You sold Bicicleta and 2 more items for R$50.00. Time to ship it."
    )
    alert = {"count": 1, "params": {"q": "bike", "category": "esportes"}}
    assert texts.sentence("saved_search", alert, "pt-BR") == (
        "1 anúncio novo para a sua busca “bike” · esportes."
    )
    assert texts.target("saved_search", alert) == "/search?q=bike&category=esportes&sort=newest"
    assert texts.target("order_paid", payload) == "/seller"
    assert texts.target("order_shipped", payload) == "/orders"
    assert texts.target("new_message", {"conversation_id": "c"}) == "/inbox/c"
    # Every kind that leaves the app has a sentence in both languages.
    for kind in texts.CATEGORY_OF:
        for lang in texts.LANGUAGES:
            assert texts.sentence(kind, {"count": 2}, lang), (kind, lang)


# --- a browser that receives pushes -------------------------------------------------------


class Device:
    """A browser's push subscription: its own key pair, and the messages it received."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.endpoint = ENDPOINT + uuid.uuid4().hex
        self.received: list[dict[str, Any]] = []

    def subscription(self) -> dict[str, Any]:
        public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": b64url_encode(public), "auth": b64url_encode(self.auth)},
        }

    def decrypt(self, body: bytes) -> dict[str, Any]:
        salt, length = body[:16], body[20]
        sender_public, ciphertext = body[21 : 21 + length], body[21 + length :]
        sender = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public)
        shared = self.key.exchange(ec.ECDH(), sender)
        receiver_public = b64url_decode(self.subscription()["keys"]["p256dh"])

        def hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
            return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)

        ikm = hkdf(self.auth, shared, b"WebPush: info\x00" + receiver_public + sender_public, 32)
        key = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(key).decrypt(nonce, ciphertext, None)
        assert plain.endswith(b"\x02")
        message: dict[str, Any] = json.loads(plain[:-1])
        return message


class PushService:
    """Stands in for the browser vendors' push services."""

    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self.answer = 201

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["content-encoding"] == "aes128gcm"
        assert request.headers["authorization"].startswith("vapid t=")
        device = self.devices[str(request.url)]
        if self.answer < 300:
            device.received.append(device.decrypt(request.content))
        return httpx.Response(self.answer)

    def device(self) -> Device:
        d = Device()
        self.devices[d.endpoint] = d
        return d


def enable(app: Any) -> tuple[PushService, LogEmail]:
    service, mail = PushService(), LogEmail()
    client = httpx.AsyncClient(transport=httpx.MockTransport(service.handler))
    push = WebPush(generate_vapid_private_key(), "mailto:ops@example.com", client=client)
    app.state.jobs = dataclasses.replace(app.state.jobs, push=push, email=mail)
    return service, mail


async def deliver(app: Any) -> int:
    ctx = app.state.jobs
    async with app.state.db.sessionmaker() as db, db.begin():
        return await send_due(db, ctx.push, ctx.email, "https://shop.example")


async def subscribe(client: Any, device: Device) -> None:
    response = await client.post("/api/me/push-subscriptions", json=device.subscription())
    assert response.status_code == 204, response.text


async def deliveries(app: Any) -> list[tuple[str, str, str, str | None]]:
    rows = await sql(
        app,
        "SELECT n.kind, d.channel, d.status, d.error FROM notification_deliveries d"
        " JOIN notifications n ON n.id = d.notification_id ORDER BY d.id",
    )
    return [tuple(r) for r in rows]


# --- end to end ---------------------------------------------------------------------------


async def test_a_message_reaches_the_buyers_phone_encrypted(
    app: Any, seller: Any, buyer: Any
) -> None:
    service, _ = enable(app)
    phone = service.device()
    await subscribe(buyer, phone)
    item = await create_listing(seller, title="Bicicleta aro 29")
    chat = await start(buyer, item["id"], "Ainda disponível?")
    assert (await say(seller, chat["id"], "Sim!")).status_code == 201

    assert await deliver(app) == 1
    [message] = phone.received
    assert message["title"] == "Nova mensagem"
    assert message["body"] == "Seller mandou mensagem sobre Bicicleta aro 29."
    assert message["url"] == f"/inbox/{chat['id']}"
    assert message["tag"] == chat["id"]
    # The seller has no device: nothing was queued for them.
    assert await deliveries(app) == [("new_message", "push", "sent", None)]
    assert await deliver(app) == 0  # sent once


async def test_a_sale_e_mails_both_sides_in_their_language(
    app: Any, seller: Any, buyer: Any
) -> None:
    _, mail = enable(app)
    await buyer.patch("/api/auth/me", json={"locale": "en"})
    await sold(seller, buyer, title="Luminária", price_cents=8000)
    await deliver(app)

    by_subject = {m.subject: m for m in mail.sent}
    assert set(by_subject) == {
        "Você vendeu Luminária por R$ 130,00. Agora é só enviar.",
        "Payment confirmed: Luminária.",
        "Luminária has shipped. Tracking: BR123456789.",
    }
    seller_mail = by_subject["Você vendeu Luminária por R$ 130,00. Agora é só enviar."]
    assert "https://shop.example/seller" in seller_mail.text
    assert 'href="https://shop.example/seller"' in seller_mail.html
    assert "<b>" not in seller_mail.html
    assert "Luminária" in seller_mail.html
    statuses = {(kind, channel, status) for kind, channel, status, _ in await deliveries(app)}
    assert ("order_paid", "email", "sent") in statuses
    assert all(channel == "email" for _, channel, _ in statuses)  # nobody has a device


async def test_preferences_decide_what_is_queued(app: Any, seller: Any, buyer: Any) -> None:
    service, mail = enable(app)
    await subscribe(buyer, service.device())
    prefs = (await buyer.get("/api/me/notification-preferences")).json()
    assert prefs["push_available"]
    assert prefs["email_available"]
    assert prefs["devices"] == 1
    assert {c["category"]: (c["push"], c["email"]) for c in prefs["categories"]} == {
        "messages": (True, False),
        "offers": (True, False),
        "orders": (True, True),
        "saved": (True, False),
        "activity": (False, False),
    }
    # E-mail is only for orders: asking for it elsewhere is stored but never sent.
    for category, push, email in (("messages", False, True), ("orders", True, False)):
        r = await buyer.put(
            "/api/me/notification-preferences",
            json={"category": category, "push": push, "email": email},
        )
        assert r.status_code == 200
    item = await create_listing(seller)
    chat = await start(buyer, item["id"], "Oi")
    await say(seller, chat["id"], "Olá")
    await sold(seller, buyer, shipped=False, title="Mesa de centro")
    await deliver(app)
    queued = {(kind, channel) for kind, channel, _, _ in await deliveries(app)}
    assert ("new_message", "push") not in queued
    assert ("new_message", "email") not in queued
    assert ("purchase_confirmed", "push") in queued
    assert ("purchase_confirmed", "email") not in queued
    assert ("order_paid", "email") in queued  # the seller kept the default
    assert [m.subject for m in mail.sent] == [
        "Você vendeu Mesa de centro por R$ 1.250,00. Agora é só enviar."
    ]
    bad = await buyer.put(
        "/api/me/notification-preferences", json={"category": "spam", "push": True, "email": True}
    )
    assert bad.status_code == 422


async def test_gone_devices_are_dropped_and_failures_retried(
    app: Any, seller: Any, buyer: Any
) -> None:
    service, _ = enable(app)
    await subscribe(buyer, service.device())
    item = await create_listing(seller)

    service.answer = 503
    chat = await start(buyer, item["id"], "Oi")
    await say(seller, chat["id"], "Olá")
    assert await deliver(app) == 0
    [(_, _, status, error)] = await deliveries(app)
    assert (status, error) == ("pending", "HTTP 503")
    assert await sql(app, "SELECT attempts FROM notification_deliveries") == [(1,)]
    assert await deliver(app) == 0  # not due yet: the retry waits

    service.answer = 410
    await sql(app, "UPDATE notification_deliveries SET next_attempt_at = now()")
    await deliver(app)
    assert await deliveries(app) == [("new_message", "push", "skipped", "no device")]
    assert await sql(app, "SELECT count(*) FROM push_subscriptions") == [(0,)]


async def test_a_notice_read_in_the_app_is_not_pushed(app: Any, seller: Any, buyer: Any) -> None:
    service, _ = enable(app)
    phone = service.device()
    await subscribe(buyer, phone)
    item = await create_listing(seller)
    chat = await start(buyer, item["id"], "Oi")
    await say(seller, chat["id"], "Olá")
    await buyer.post("/api/notifications/read", json={"all": True})
    await deliver(app)
    assert phone.received == []
    assert await deliveries(app) == [("new_message", "push", "skipped", "read in the app")]


async def test_subscriptions_belong_to_one_person_and_one_push_service(
    app: Any, seller: Any, buyer: Any
) -> None:
    service, _ = enable(app)
    shared = service.device()
    await subscribe(buyer, shared)
    await subscribe(seller, shared)  # same browser, another account signed in
    owners = await sql(app, "SELECT count(*) FROM push_subscriptions")
    assert owners == [(1,)]
    assert (await buyer.get("/api/me/notification-preferences")).json()["devices"] == 0
    assert (await seller.post("/api/me/push-subscriptions/test")).json() == {"sent": 1, "failed": 0}
    assert shared.received[-1]["body"] == "Os avisos estão funcionando neste aparelho."

    sneaky = shared.subscription() | {"endpoint": "https://169.254.169.254/latest/meta-data"}
    r = await buyer.post("/api/me/push-subscriptions", json=sneaky)
    assert r.status_code == 422
    plain = shared.subscription() | {"endpoint": "http://fcm.googleapis.com/fcm/send/x"}
    assert (await buyer.post("/api/me/push-subscriptions", json=plain)).status_code == 422

    removed = await seller.post(
        "/api/me/push-subscriptions/remove", json={"endpoint": shared.endpoint}
    )
    assert removed.status_code == 204
    assert await sql(app, "SELECT count(*) FROM push_subscriptions") == [(0,)]


async def test_without_keys_or_smtp_nothing_leaves(app: Any, seller: Any, buyer: Any) -> None:
    # The default configuration: no VAPID key, no e-mail service.
    app.state.jobs = dataclasses.replace(app.state.jobs, push=None, email=NoEmail())
    prefs = (await buyer.get("/api/me/notification-preferences")).json()
    assert prefs["push_available"] is False
    assert prefs["email_available"] is False
    service = PushService()
    r = await buyer.post("/api/me/push-subscriptions", json=service.device().subscription())
    assert r.json()["error"]["code"] == "push_unavailable"
    await sold(seller, buyer, shipped=False, title="Vaso de barro")
    await deliver(app)
    assert {status for _, _, status, _ in await deliveries(app)} == {"skipped"}


async def test_signing_out_stops_pushes_to_that_device(app: Any, seller: Any, buyer: Any) -> None:
    service, _ = enable(app)
    phone = service.device()
    await subscribe(buyer, phone)
    item = await create_listing(seller)
    chat = await start(buyer, item["id"], "Oi")
    assert (await buyer.post("/api/auth/logout")).status_code in (200, 204)
    await say(seller, chat["id"], "Olá")
    await deliver(app)
    assert phone.received == []
    assert await deliveries(app) == [("new_message", "push", "skipped", "no device")]
