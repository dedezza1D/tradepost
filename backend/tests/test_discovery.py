"""Discovery: similar listings, search suggestions and saved searches with alerts."""

from datetime import UTC, datetime, timedelta
from typing import Any

from tests.conftest import LISTING, create_listing
from tests.test_checkout import sql
from tests.test_semantic import index, use_fake_ai
from tradepost.saved_searches import send_alerts


async def similar_titles(client: Any, listing_id: str) -> list[str]:
    response = await client.get(f"/api/listings/{listing_id}/similar")
    assert response.status_code == 200, response.text
    return [card["title"] for card in response.json()]


async def test_similar_listings_without_a_model_use_category_and_title(
    seller: Any, client: Any
) -> None:
    bike = await create_listing(seller)
    await create_listing(seller, title="Bicicleta aro 26 urbana")
    await create_listing(seller, title="Capacete de ciclismo", category_slug="sports")
    titles = await similar_titles(client, bike["id"])
    assert titles == ["Bicicleta aro 26 urbana"]  # same category, never the listing itself
    missing = await client.get("/api/listings/00000000-0000-0000-0000-000000000000/similar")
    assert missing.status_code == 404


async def test_similar_listings_with_a_model_follow_meaning_across_categories(
    app: Any, seller: Any, client: Any
) -> None:
    bike = await create_listing(seller)
    await create_listing(
        seller,
        title="Magrela de passeio",
        description=LISTING["description"],
        category_slug="sports",
    )
    await create_listing(
        seller,
        title="Panela de pressão inox",
        description="Cinco litros, válvula nova.",
        category_slug="home",
    )
    await index(app, use_fake_ai(app))
    assert await similar_titles(client, bike["id"]) == ["Magrela de passeio"]


async def test_suggestions_complete_what_is_typed(seller: Any, client: Any) -> None:
    await create_listing(seller)
    await create_listing(seller, title="Bicicleta Caloi aro 26")
    await create_listing(seller, title="bicicleta caloi aro 26")  # same title: suggested once
    await create_listing(seller, title="Rascunho de bicicleta", publish=False)
    response = await client.get("/api/search/suggest", params={"q": "bici"})
    assert response.status_code == 200, response.text
    assert sorted(q.casefold() for q in response.json()["queries"]) == [
        "bicicleta aro 29 seminova",
        "bicicleta caloi aro 26",
    ]
    assert (await client.get("/api/search/suggest", params={"q": "%"})).status_code == 422
    nothing = await client.get("/api/search/suggest", params={"q": "%%"})
    assert nothing.json()["queries"] == []  # wildcards are not a pattern


async def test_saved_searches_alert_once_about_new_listings(
    app: Any, seller: Any, buyer: Any
) -> None:
    body = {"q": " Bicicleta ", "state": "PE"}
    first = await buyer.post("/api/me/saved-searches", json=body)
    again = await buyer.post("/api/me/saved-searches", json={"q": "bicicleta", "state": "PE"})
    assert first.status_code == again.status_code == 200, first.text
    assert first.json()["id"] == again.json()["id"]  # the same search is kept once
    assert first.json()["params"] == {"q": "Bicicleta", "state": "PE"}
    assert (await buyer.post("/api/me/saved-searches", json={"state": "PE"})).status_code == 422
    # The seller follows the same search: their own listings never alert them.
    assert (await seller.post("/api/me/saved-searches", json=body)).status_code == 200

    await create_listing(seller)
    await create_listing(seller, title="Panela de pressão", category_slug="home")

    async def run_alerts(after: timedelta) -> int:
        async with app.state.db.sessionmaker() as db, db.begin():
            return await send_alerts(db, now=datetime.now(UTC) + after)

    assert await run_alerts(timedelta(minutes=1)) == 0  # not yet due
    assert await run_alerts(timedelta(minutes=11)) == 1
    payloads = await sql(app, "SELECT payload FROM notifications WHERE kind = 'saved_search'")
    assert [p[0]["count"] for p in payloads] == [1]
    assert await run_alerts(timedelta(minutes=30)) == 0  # nothing new since the last check

    listed = (await buyer.get("/api/me/saved-searches")).json()
    assert len(listed) == 1
    path = f"/api/me/saved-searches/{listed[0]['id']}"
    assert (await seller.delete(path)).status_code == 404  # not theirs
    assert (await buyer.delete(path)).status_code == 204
    assert (await buyer.get("/api/me/saved-searches")).json() == []
