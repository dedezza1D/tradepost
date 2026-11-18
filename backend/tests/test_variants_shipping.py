"""Variants with their own stock, and calculated shipping."""

import asyncio
import uuid
from typing import Any

from tests.conftest import create_listing, make_client, register
from tradepost.shipping import Parcel, TableShipping

PACKAGE = {
    "shipping_mode": "calculated",
    "weight_grams": 1500,
    "length_cm": 40,
    "width_cm": 30,
    "height_cm": 20,
}


async def set_variants(seller: Any, listing_id: str, variants: list[dict[str, Any]]) -> Any:
    return await seller.put(f"/api/listings/{listing_id}/variants", json={"variants": variants})


async def checkout(client: Any, lines: list[dict[str, Any]], **extra: Any) -> Any:
    return await client.post(
        "/api/checkouts",
        json={"provider": "fake", "items": lines, **extra},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )


# --- variants --------------------------------------------------------------------------------


async def test_each_variant_has_its_own_stock(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, stock=1)
    response = await set_variants(
        seller, item["id"], [{"name": "M", "stock": 2}, {"name": "G", "stock": 1}]
    )
    assert response.status_code == 200, response.text
    listing = response.json()
    assert listing["stock"] == 3
    medium, large = listing["variants"]
    assert (medium["name"], large["name"]) == ("M", "G")

    missing = await checkout(buyer, [{"listing_id": item["id"], "quantity": 1}])
    assert missing.json()["error"]["code"] == "variant_required"

    bought = await checkout(
        buyer, [{"listing_id": item["id"], "variant_id": medium["id"], "quantity": 2}]
    )
    assert bought.status_code == 201, bought.text
    assert bought.json()["orders"][0]["items"][0]["variant_name"] == "M"
    after = (await seller.get(f"/api/listings/{item['id']}")).json()
    assert after["stock"] == 1
    assert after["status"] == "active"
    assert [v["stock"] for v in after["variants"]] == [0, 1]

    none_left = await checkout(
        buyer, [{"listing_id": item["id"], "variant_id": medium["id"], "quantity": 1}]
    )
    assert none_left.json()["error"]["code"] == "out_of_stock"

    # Cancelling the checkout gives the option its stock back.
    await buyer.post(f"/api/checkouts/{bought.json()['id']}/cancel")
    restored = (await seller.get(f"/api/listings/{item['id']}")).json()
    assert [v["stock"] for v in restored["variants"]] == [2, 1]
    assert restored["stock"] == 3


async def test_the_last_unit_of_an_option_is_sold_once(app: Any, seller: Any) -> None:
    item = await create_listing(seller)
    variant = (await set_variants(seller, item["id"], [{"name": "Azul", "stock": 1}])).json()[
        "variants"
    ][0]
    async with make_client(app) as a, make_client(app) as b:
        await register(a, display_name="Buyer A")
        await register(b, display_name="Buyer B")
        line = [{"listing_id": item["id"], "variant_id": variant["id"], "quantity": 1}]
        results = await asyncio.gather(checkout(a, line), checkout(b, line))
    assert sorted(r.status_code for r in results) == [201, 409]
    final = (await seller.get(f"/api/listings/{item['id']}")).json()
    assert final["status"] == "sold_out"
    assert final["variants"][0]["stock"] == 0


async def test_variant_rules(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    listing = (
        await set_variants(
            seller, item["id"], [{"name": "P", "stock": 1}, {"name": "M", "stock": 1}]
        )
    ).json()
    dup = await set_variants(
        seller, item["id"], [{"name": "P", "stock": 1}, {"name": "p", "stock": 1}]
    )
    assert dup.json()["error"]["code"] == "duplicate_variant"
    stock = await seller.patch(
        f"/api/listings/{item['id']}", json={"version": listing["version"], "stock": 9}
    )
    assert stock.json()["error"]["code"] == "stock_from_variants"

    # A cart line keeps its option; retiring the option makes it unbuyable.
    small = listing["variants"][0]
    assert (
        await buyer.put(f"/api/cart/{item['id']}", json={"quantity": 1, "variant_id": small["id"]})
    ).status_code == 204
    medium = listing["variants"][1]
    await buyer.put(f"/api/cart/{item['id']}", json={"quantity": 1, "variant_id": medium["id"]})
    cart = (await buyer.get("/api/cart")).json()
    assert sorted(i["variant_name"] for i in cart["items"]) == ["M", "P"]
    kept = (
        await set_variants(seller, item["id"], [{"id": medium["id"], "name": "M", "stock": 1}])
    ).json()
    assert [v["name"] for v in kept["variants"]] == ["M"]
    from_cart = await buyer.post(
        "/api/checkouts",
        json={"provider": "fake", "from_cart": True},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert from_cart.json()["error"]["code"] == "unknown_variant"


# --- shipping --------------------------------------------------------------------------------


def test_the_table_bills_the_larger_of_real_and_cubic_weight() -> None:
    light_but_bulky = Parcel(weight_grams=500, length_cm=60, width_cm=50, height_cm=40)
    assert light_but_bulky.billable_kg(1) == 20.0  # 60*50*40/6000
    table = TableShipping()
    near = {q.service: q for q in table.quote(5, Parcel(800, 20, 15, 10), 1)}
    far = {q.service: q for q in table.quote(3000, Parcel(800, 20, 15, 10), 1)}
    assert near["standard"].price_cents == 990
    assert near["standard"].days == 1
    assert far["standard"].price_cents > near["standard"].price_cents
    assert far["express"].price_cents > far["standard"].price_cents
    assert far["express"].days < far["standard"].days


async def test_calculated_shipping_needs_the_package(seller: Any) -> None:
    response = await seller.post(
        "/api/listings",
        json={
            "title": "Mesa de jantar",
            "description": "Mesa de madeira maciça com seis lugares.",
            "category_slug": "home",
            "condition": "used",
            "price_cents": 90000,
            "stock": 1,
            "city": "Recife",
            "state": "PE",
            "shipping_mode": "calculated",
        },
    )
    assert response.json()["error"]["code"] == "package_required"


async def test_shipping_is_quoted_by_distance_and_charged_at_checkout(
    app: Any, seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller, **PACKAGE)
    url = f"/api/listings/{item['id']}/shipping"
    recife = (await buyer.get("/api/geo/cities", params={"q": "Recife"})).json()[0]["ibge_code"]
    porto_alegre = (await buyer.get("/api/geo/cities", params={"q": "Porto Alegre"})).json()[0][
        "ibge_code"
    ]
    near = (await buyer.get(url, params={"to": recife})).json()
    far = (await buyer.get(url, params={"to": porto_alegre})).json()
    assert near["destination"] == "Recife - PE"
    near_std = next(o for o in near["options"] if o["service"] == "standard")
    far_std = next(o for o in far["options"] if o["service"] == "standard")
    assert far_std["price_cents"] > near_std["price_cents"]
    assert far_std["days"] > near_std["days"]

    lines = [{"listing_id": item["id"], "quantity": 1}]
    quote = await buyer.post(
        "/api/checkouts/quote",
        json={"items": lines, "ship_to": porto_alegre, "shipping_service": "express"},
    )
    assert quote.status_code == 200, quote.text
    quoted = quote.json()
    far_express = next(o for o in far["options"] if o["service"] == "express")
    assert quoted["orders"][0]["shipping_cents"] == far_express["price_cents"]
    assert set(quoted["shipping_by_service"]) == {"standard", "express"}

    created = await checkout(buyer, lines, ship_to=porto_alegre, shipping_service="express")
    assert created.status_code == 201, created.text
    order = created.json()["orders"][0]
    assert created.json()["total_cents"] == quoted["total_cents"]
    assert order["shipping_cents"] == far_express["price_cents"]
    assert order["shipping_service"] == "express"
    assert order["shipping_days"] == far_express["days"]
    assert order["ship_to"] == "Porto Alegre - RS"


async def test_calculated_shipping_defaults_to_the_buyers_city(app: Any, seller: Any) -> None:
    item = await create_listing(seller, **PACKAGE)
    lines = [{"listing_id": item["id"], "quantity": 1}]
    async with make_client(app) as homeless:
        await register(homeless, display_name="No City", city=None, state=None)
        missing = await homeless.post("/api/checkouts/quote", json={"items": lines})
        assert missing.json()["error"]["code"] == "destination_required"
    async with make_client(app) as local:
        await register(local, display_name="Local Buyer")
        quote = (await local.post("/api/checkouts/quote", json={"items": lines})).json()
        assert quote["destination"] == "Recife - PE"
        assert quote["orders"][0]["shipping_days"] == 1  # same city


async def test_offers_on_listings_with_variants_name_the_option(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, price_cents=10000)
    variant = (await set_variants(seller, item["id"], [{"name": "42", "stock": 1}])).json()[
        "variants"
    ][0]
    conversation = (await buyer.post("/api/conversations", json={"listing_id": item["id"]})).json()
    url = f"/api/conversations/{conversation['id']}/offers"
    assert (await buyer.post(url, json={"amount_cents": 9000})).json()["error"][
        "code"
    ] == "variant_required"
    offer = (await buyer.post(url, json={"amount_cents": 9000, "variant_id": variant["id"]})).json()
    assert offer["variant_name"] == "42"
    await seller.post(f"/api/offers/{offer['id']}/accept")
    paid = await buyer.post(
        f"/api/offers/{offer['id']}/checkout",
        json={"provider": "fake"},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    line = paid.json()["orders"][0]["items"][0]
    assert (line["variant_name"], line["unit_price_cents"]) == ("42", 9000)
