"""Semantic search: embeddings kept current by a job, fused with full-text search."""

from typing import Any

from tests.conftest import create_listing
from tests.test_checkout import sql
from tradepost.ai.client import AI, EmbedResult, FakeProvider
from tradepost.catalog.semantic import embed_pending, fuse
from tradepost.config import get_settings


class SynonymProvider(FakeProvider):
    """The fake embedding, with a few words mapped to the same meaning, the way a real model
    places "magrela" (slang) next to "bicicleta"."""

    SYNONYMS = {"magrela": "bicicleta", "bike": "bicicleta"}

    def __init__(self) -> None:
        super().__init__(dimensions=256)  # few hash collisions between unrelated words

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        mapped = [
            " ".join(self.SYNONYMS.get(w, w) for w in t.lower().replace(".", " ").split())
            for t in texts
        ]
        return await super().embed(model, mapped)


def use_fake_ai(app: Any) -> AI:
    settings = get_settings().model_copy(
        # The fake embedding spreads distances wider than a real model, hence its own cutoff.
        update={"ai_provider": "fake", "ai_embed_model": "fake-embed", "search_max_distance": 0.75}
    )
    ai = AI(settings, app.state.db, SynonymProvider())
    app.state.ai = ai
    return ai


async def index(app: Any, ai: AI) -> int:
    async with app.state.db.sessionmaker() as db, db.begin():
        return await embed_pending(db, ai)


def test_fusion_rewards_agreement_and_respects_weights() -> None:
    a, b, c = "a", "b", "c"
    assert fuse([([a, b], 1.0), ([b, c], 1.0)], 60)[0] == b  # in both lists
    assert fuse([([a], 1.0), ([c], 3.0)], 60) == [c, a]  # the heavier list wins a tie of ranks


async def test_listings_are_embedded_once_and_again_after_an_edit(app: Any, seller: Any) -> None:
    ai = use_fake_ai(app)
    listing = await create_listing(seller)
    await create_listing(seller, publish=False, title="Rascunho")  # drafts are not indexed
    assert await index(app, ai) == 1
    assert await index(app, ai) == 0  # unchanged: nothing to do
    edited = await seller.patch(
        f"/api/listings/{listing['id']}",
        json={"title": "Bike aro 26", "version": listing["version"]},
    )
    assert edited.status_code == 200, edited.text
    assert await index(app, ai) == 1
    assert await sql(app, "SELECT count(*) FROM listing_embeddings") == [(1,)]


async def test_hybrid_search_finds_meaning_the_words_miss(
    app: Any, seller: Any, client: Any
) -> None:
    bike = await create_listing(seller)  # "Bicicleta aro 29 seminova"
    await create_listing(
        seller,
        title="Panela de pressão inox",
        description="Cinco litros, válvula nova.",
        category_slug="home",
    )

    async def titles(q: str) -> list[str]:
        response = await client.get("/api/listings", params={"q": q})
        assert response.status_code == 200, response.text
        return [item["title"] for item in response.json()["items"]]

    assert await titles("magrela") == []  # full-text only: the word is not in any listing

    ai = use_fake_ai(app)
    await index(app, ai)
    assert await titles("magrela") == [bike["title"]]  # the pan is too far to be returned
    assert (await titles("panela"))[0] == "Panela de pressão inox"

    # No listing has the word: the page says its results are similar items, not matches.
    similar = (await client.get("/api/listings", params={"q": "magrela"})).json()
    exact = (await client.get("/api/listings", params={"q": "panela"})).json()
    assert (similar["approximate"], exact["approximate"]) == (True, False)

    # Other sorts keep the plain search, whose order the user chose.
    newest = await client.get("/api/listings", params={"q": "magrela", "sort": "newest"})
    assert newest.json()["items"] == []
