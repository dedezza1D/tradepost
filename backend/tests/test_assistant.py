"""Listing assistant: drafts from photos, price ranges from similar listings, and tips."""

import base64
import io
from typing import Any

from PIL import Image

from tests.conftest import create_listing, image_bytes
from tests.test_semantic import use_fake_ai
from tradepost.ai.client import FakeProvider
from tradepost.assistant import clean_draft, price_range


def answer(app: Any, payload: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Make the fake model answer drafts with ``payload``; returns the messages it received."""
    ai = use_fake_ai(app)
    seen: list[list[dict[str, Any]]] = []
    provider = ai.provider
    assert isinstance(provider, FakeProvider)

    def respond(messages: list[dict[str, Any]]) -> dict[str, Any]:
        seen.append(messages)
        return payload

    provider.responders["Você"] = respond  # the draft prompt's first word
    return seen


DRAFT = {
    "is_product": True,
    "title": "Bicicleta aro 29 preta",
    "description": "Quadro preto com detalhes vermelhos. Funcionando perfeitamente. Freio a disco.",
    "category": "bicicletas",
    "condition": "used",
    "brand": "Caloi",
    "color": "Preto",
}


def test_drafts_drop_claims_a_photo_cannot_support() -> None:
    draft = clean_draft({**DRAFT, "category": "spaceships"})
    assert draft.description == "Quadro preto com detalhes vermelhos. Freio a disco."
    assert draft.removed_claims == 1
    assert draft.category_slug is None  # unknown categories are left for the seller
    assert draft.color == "preto"


def test_price_range_ignores_outliers() -> None:
    low, median, high = price_range([100_00, 110_00, 120_00, 130_00, 140_00, 1_200_00])
    assert (low, median, high) == (11000, 12000, 13000)


async def test_draft_from_photos(app: Any, seller: Any) -> None:
    files = [("files", ("a.png", image_bytes(size=(2000, 1500)), "image/png"))] * 4
    unavailable = await seller.post("/api/assist/draft", files=files[:1])
    assert unavailable.status_code == 503  # no model configured
    assert (await seller.get("/api/assist/status")).json() == {"draft": False}

    seen = answer(app, DRAFT)
    assert (await seller.get("/api/assist/status")).json() == {"draft": True}
    response = await seller.post("/api/assist/draft", files=files)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Bicicleta aro 29 preta"
    assert body["category_slug"] == "bikes"
    assert "Funcionando" not in body["description"]
    images = seen[0][1]["images"]
    assert len(images) == 3  # at most three photos reach the model
    # Photos are shrunk before they reach the model.

    with Image.open(io.BytesIO(base64.b64decode(images[0]))) as image:
        assert max(image.size) == 768

    bad = await seller.post("/api/assist/draft", files=[("files", ("x.png", b"no", "image/png"))])
    assert bad.status_code == 400

    answer(app, {**DRAFT, "is_product": False, "title": "Paisagem"})
    not_product = await seller.post(
        "/api/assist/draft", files=[("files", ("b.png", image_bytes(size=(90, 60)), "image/png"))]
    )
    assert not_product.status_code == 422
    assert not_product.json()["error"]["code"] == "not_a_product"


async def test_price_comes_from_similar_listings(seller: Any, client: Any) -> None:
    for price in (900_00, 1000_00, 1100_00, 1200_00, 9_900_00):
        await create_listing(seller, title="Bicicleta aro 29 Caloi", price_cents=price)
    await create_listing(seller, title="Panela de pressão", category_slug="home", price_cents=50_00)

    body = {"title": "Bicicleta Caloi aro 29", "category_slug": "bikes"}
    assert (await client.post("/api/assist/price", json=body)).status_code == 401
    suggestion = (await seller.post("/api/assist/price", json=body)).json()
    assert suggestion["basis"] == "similar"
    assert suggestion["count"] == 5
    assert 900_00 <= suggestion["low_cents"] <= suggestion["median_cents"]
    assert suggestion["high_cents"] <= 1200_00  # the listing with an extra zero is ignored

    # Nothing similar by words: the category's prices, said as such.
    fallback = await seller.post(
        "/api/assist/price", json={"title": "Magrela vermelha", "category_slug": "bikes"}
    )
    assert fallback.json()["basis"] == "category"
    nothing = await seller.post("/api/assist/price", json={"title": "Algo raro"})
    assert nothing.json() is None


async def test_tips_point_at_what_to_improve(seller: Any, buyer: Any) -> None:
    for price in (900_00, 1000_00, 1100_00):
        await create_listing(seller, title="Bicicleta aro 29 Caloi", price_cents=price)
    listing = await create_listing(
        seller,
        title="BICICLETA CALOI ARO 29",
        description="Aro 29 usada.",
        price_cents=3000_00,
        publish=False,
    )
    dark = io.BytesIO()
    Image.new("RGB", (64, 48), (12, 12, 14)).save(dark, format="PNG")
    await seller.post(
        f"/api/listings/{listing['id']}/photos",
        files={"file": ("d.png", dark.getvalue(), "image/png")},
    )
    path = f"/api/assist/listings/{listing['id']}/tips"
    assert (await buyer.get(path)).status_code == 404  # only the seller sees them
    tips = {tip["code"]: tip["data"] for tip in (await seller.get(path)).json()}
    assert set(tips) == {
        "few_photos",
        "dark_photo",
        "shouting_title",
        "short_description",
        "price_above_similar",
    }
    assert tips["few_photos"] == {"count": 1}
    assert tips["price_above_similar"]["high_cents"] <= 1100_00

    vague = await create_listing(seller, title="Bike 29", price_cents=1000_00)
    codes = [
        t["code"] for t in (await seller.get(f"/api/assist/listings/{vague['id']}/tips")).json()
    ]
    assert "short_title" in codes
    assert "dark_photo" not in codes


async def test_a_looping_answer_is_retried_with_a_repeat_penalty(app: Any, seller: Any) -> None:
    ai = use_fake_ai(app)
    provider = ai.provider
    assert isinstance(provider, FakeProvider)
    calls: list[int] = []

    def flaky(messages: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("prediction aborted, token repeat limit reached")
        return DRAFT

    provider.responders["Você"] = flaky
    files = [("files", ("a.png", image_bytes(), "image/png"))]
    response = await seller.post("/api/assist/draft", files=files)
    assert response.status_code == 200, response.text
    assert len(calls) == 2
