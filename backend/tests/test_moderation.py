"""Moderation: listings held for review, report triage and scam warnings in chat."""

from typing import Any

from tests.conftest import create_listing
from tests.test_checkout import sql
from tests.test_ops import admin_client
from tests.test_semantic import use_fake_ai
from tradepost.ai.client import FakeProvider
from tradepost.moderation import listing_rules, message_rules
from tradepost.moderation_queue import screen_messages, triage_reports


def test_rules_catch_prohibited_items_and_spare_look_alikes() -> None:
    assert listing_rules("Revólver calibre 38", "Entrego em mãos.") == "weapons"
    assert listing_rules("Pod descartável 5000 puffs", "Lacrado.") == "tobacco"
    assert listing_rules("Rivotril 2mg", "Sem receita.") == "drugs"
    assert listing_rules("Pistola de cola quente", "Para artesanato.") is None
    assert listing_rules("Pelúcia de arara-azul", "40 cm.") is None
    assert listing_rules("Bicicleta aro 29", "Quadro de alumínio.") is None
    assert message_rules("Faz um Pix direto pra mim que te dou desconto")
    assert not message_rules("Ainda está disponível?")


def answer_with(
    app: Any, listing: dict[str, Any] | None = None, message: dict[str, Any] | None = None
) -> None:
    """Fake model: every moderation prompt starts with "Você"; the second word tells them apart."""
    ai = use_fake_ai(app)
    provider = ai.provider
    assert isinstance(provider, FakeProvider)

    def respond(messages: list[dict[str, Any]]) -> dict[str, Any]:
        second = str(messages[0]["content"]).split()[1]
        if second == "modera":
            return listing or {"category": "none", "risk": "low", "reason": ""}
        if second == "protege":
            return message or {"scam": False, "kind": "none", "reason": ""}
        return {"category": "scam", "priority": 1, "reason": "Pede pagamento fora do site."}

    provider.responders["Você"] = respond


async def test_a_prohibited_listing_waits_for_review(app: Any, seller: Any, client: Any) -> None:
    held = await create_listing(
        seller,
        publish=False,
        title="Revólver calibre 38",
        description="Entrego em mãos, sem documento.",
    )
    await seller.post(
        f"/api/listings/{held['id']}/photos", files={"file": ("p.png", _png(), "image/png")}
    )
    published = await seller.post(f"/api/listings/{held['id']}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "in_review"
    assert (await client.get("/api/listings", params={"q": "revólver"})).json()["items"] == []
    assert (await client.get(f"/api/listings/{held['id']}")).status_code == 404  # not public yet

    admin = await admin_client(app)
    try:
        queue = (await admin.get("/api/admin/review/listings")).json()
        assert [(i["id"], i["review_category"]) for i in queue] == [(held["id"], "weapons")]
        assert (await seller.get("/api/admin/review/listings")).status_code == 403
        rejected = await admin.post(
            f"/api/admin/review/listings/{held['id']}", json={"decision": "reject"}
        )
        assert rejected.json() == {"status": "removed"}
        again = await admin.post(
            f"/api/admin/review/listings/{held['id']}", json={"decision": "approve"}
        )
        assert again.status_code == 400  # already decided
    finally:
        await admin.aclose()
    kinds = await sql(app, "SELECT kind FROM notifications WHERE kind LIKE 'listing_%'")
    assert kinds == [("listing_removed",)]


async def test_the_model_can_hold_what_the_words_miss_and_admins_approve(
    app: Any, seller: Any
) -> None:
    answer_with(app, listing={"category": "drugs", "risk": "high", "reason": "Gíria para droga."})
    held = await create_listing(
        seller, title="Balinha para festas", description="Entrego no evento."
    )
    assert held["status"] == "in_review"

    answer_with(app)  # the model sees nothing wrong
    fine = await create_listing(seller, title="Bicicleta aro 29", description="Quadro de alumínio.")
    assert fine["status"] == "active"

    admin = await admin_client(app)
    try:
        approved = await admin.post(
            f"/api/admin/review/listings/{held['id']}", json={"decision": "approve"}
        )
        assert approved.json() == {"status": "active"}
    finally:
        await admin.aclose()
    assert await sql(app, "SELECT kind FROM notifications WHERE kind LIKE 'listing_%'") == [
        ("listing_approved",)
    ]

    # Editing a live listing into something prohibited sends it back to review.
    edited = await seller.patch(
        f"/api/listings/{fine['id']}",
        json={"title": "Espingarda calibre 12", "version": fine["version"]},
    )
    assert edited.json()["status"] == "in_review"


async def test_reports_are_triaged_most_urgent_first(app: Any, seller: Any, buyer: Any) -> None:
    calm = await create_listing(
        seller, title="Sofá de couro marrom", description="Três lugares, retirada."
    )
    urgent = await create_listing(
        seller, title="Mesa de jantar", description="Madeira maciça, 6 lugares."
    )
    await buyer.post(
        "/api/reports", json={"target_type": "listing", "target_id": calm["id"], "reason": "other"}
    )
    await buyer.post(
        "/api/reports", json={"target_type": "listing", "target_id": urgent["id"], "reason": "scam"}
    )
    async with app.state.db.sessionmaker() as db, db.begin():
        assert await triage_reports(db, None) == 2  # rules only: no model
    admin = await admin_client(app)
    try:
        queue = (await admin.get("/api/admin/reports")).json()
    finally:
        await admin.aclose()
    assert [(r["target_label"], r["triage_priority"]) for r in queue] == [
        ("Mesa de jantar", 1),
        ("Sofá de couro marrom", 3),
    ]


async def test_chat_scams_get_a_warning_only_with_a_model(
    app: Any, seller: Any, buyer: Any
) -> None:
    listing = await create_listing(seller)
    text = "Já fiz o pagamento, segue o comprovante. Pode enviar hoje?"
    started = await buyer.post(
        "/api/conversations", json={"listing_id": listing["id"], "body": text}
    )
    assert started.status_code == 200, started.text
    async with app.state.db.sessionmaker() as db, db.begin():
        assert await screen_messages(db, app.state.ai) == 0  # no model: the word rules already ran
    answer_with(
        app, message={"scam": True, "kind": "fake_payment", "reason": "Comprovante fora do site."}
    )
    async with app.state.db.sessionmaker() as db, db.begin():
        assert await screen_messages(db, app.state.ai) == 1
        assert await screen_messages(db, app.state.ai) == 0  # each message is checked once
    assert await sql(app, "SELECT warning FROM messages") == [("fake_payment",)]


def _png() -> bytes:
    from tests.conftest import image_bytes

    return image_bytes()


async def test_the_model_clears_a_false_pix_alarm(app: Any, seller: Any, buyer: Any) -> None:
    listing = await create_listing(seller)
    text = "Aceita Pix pelo checkout do site?"
    await buyer.post("/api/conversations", json={"listing_id": listing["id"], "body": text})
    assert await sql(app, "SELECT warning FROM messages") == [("off_platform_payment",)]  # the rule
    answer_with(app)  # the model reads it as an honest question
    async with app.state.db.sessionmaker() as db, db.begin():
        assert await screen_messages(db, app.state.ai) == 1
    assert await sql(app, "SELECT warning FROM messages") == [(None,)]
