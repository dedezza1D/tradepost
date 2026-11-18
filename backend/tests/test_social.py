"""Follows, saves, alerts, the home feed, blocks, reports and public profiles."""

import uuid
from typing import Any

from sqlalchemy import text

from tests.conftest import create_listing, image_bytes, make_client, register


async def me(client: Any) -> dict[str, Any]:
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    data: dict[str, Any] = response.json()
    return data


async def kinds(client: Any) -> list[str]:
    return [n["kind"] for n in (await client.get("/api/notifications")).json()["items"]]


async def notifications(client: Any, kind: str) -> list[dict[str, Any]]:
    items = (await client.get("/api/notifications")).json()["items"]
    return [n["payload"] for n in items if n["kind"] == kind]


async def feed_ids(client: Any, tab: str, **params: Any) -> tuple[list[str], str | None]:
    response = await client.get("/api/feed", params={"tab": tab, **params})
    assert response.status_code == 200, response.text
    page = response.json()
    return [item["id"] for item in page["items"]], page["next_cursor"]


async def make_admin(app: Any, client: Any) -> None:
    user_id = (await me(client))["id"]
    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(text("UPDATE users SET is_admin = true WHERE id = :id"), {"id": user_id})


# --- follows ---------------------------------------------------------------------------------


async def test_following_is_idempotent_counted_and_announced(seller: Any, buyer: Any) -> None:
    seller_id = (await me(seller))["id"]
    first = await buyer.put(f"/api/users/{seller_id}/follow")
    again = await buyer.put(f"/api/users/{seller_id}/follow")
    assert first.status_code == again.status_code == 200
    assert again.json() == {"following": True, "follower_count": 1}

    profile = (await buyer.get(f"/api/users/{seller_id}")).json()
    assert profile["following"] is True
    assert profile["follower_count"] == 1
    assert (await buyer.get(f"/api/users/{(await me(buyer))['id']}")).json()["following_count"] == 1
    assert (await kinds(seller)).count("new_follower") == 1

    following = (await buyer.get("/api/me/following")).json()["items"]
    assert [u["id"] for u in following] == [seller_id]

    left = await buyer.delete(f"/api/users/{seller_id}/follow")
    assert left.json() == {"following": False, "follower_count": 0}
    assert (await buyer.delete(f"/api/users/{seller_id}/follow")).json()["follower_count"] == 0


async def test_following_yourself_or_nobody_is_rejected(seller: Any) -> None:
    my_id = (await me(seller))["id"]
    assert (await seller.put(f"/api/users/{my_id}/follow")).json()["error"]["code"] == "follow_self"
    assert (await seller.put(f"/api/users/{uuid.uuid4()}/follow")).status_code == 404


async def test_follow_requests_are_rate_limited(seller: Any, buyer: Any) -> None:
    seller_id = (await me(seller))["id"]
    statuses = [(await buyer.put(f"/api/users/{seller_id}/follow")).status_code for _ in range(121)]
    assert statuses[:120] == [200] * 120
    assert statuses[120] == 429


# --- saves and alerts ------------------------------------------------------------------------


async def test_saving_marks_the_listing_everywhere_and_tells_the_seller(
    app: Any, seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller)
    saved = await buyer.put(f"/api/listings/{item['id']}/save")
    assert saved.json() == {"saved": True, "save_count": 1}
    assert (await buyer.put(f"/api/listings/{item['id']}/save")).json()["save_count"] == 1

    page = (await buyer.get(f"/api/listings/{item['id']}")).json()
    assert page["saved"] is True
    assert page["save_count"] == 1
    search = (await buyer.get("/api/listings", params={"q": "bicicleta"})).json()["items"]
    assert search[0]["saved"] is True
    async with make_client(app) as anon:
        anonymous = (await anon.get(f"/api/listings/{item['id']}")).json()
    assert anonymous["saved"] is False

    mine = (await buyer.get("/api/me/saved")).json()["items"]
    assert [m["id"] for m in mine] == [item["id"]]
    assert (await notifications(seller, "listing_saved"))[0]["listing_id"] == item["id"]

    assert (await buyer.delete(f"/api/listings/{item['id']}/save")).json() == {
        "saved": False,
        "save_count": 0,
    }
    assert (await buyer.get("/api/me/saved")).json()["items"] == []


async def test_own_listings_cannot_be_saved(seller: Any) -> None:
    item = await create_listing(seller)
    response = await seller.put(f"/api/listings/{item['id']}/save")
    assert response.json()["error"]["code"] == "save_own_listing"


async def test_saving_does_not_make_the_sellers_edit_stale(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller)
    await buyer.put(f"/api/listings/{item['id']}/save")
    edit = await seller.patch(
        f"/api/listings/{item['id']}", json={"version": item["version"], "title": "Bicicleta nova"}
    )
    assert edit.status_code == 200, edit.text


async def test_price_drops_are_announced_once_per_new_low(seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, price_cents=120000)
    await buyer.put(f"/api/listings/{item['id']}/save")

    async def set_price(cents: int) -> None:
        current = (await seller.get(f"/api/listings/{item['id']}")).json()
        response = await seller.patch(
            f"/api/listings/{item['id']}",
            json={"version": current["version"], "price_cents": cents},
        )
        assert response.status_code == 200, response.text

    await set_price(100000)  # new low: alert
    await set_price(130000)  # higher: nothing
    await set_price(110000)  # lower than before, but above the low already announced
    await set_price(90000)  # new low: alert
    drops = await notifications(buyer, "saved_price_drop")
    assert [(d["old_price_cents"], d["price_cents"]) for d in drops] == [
        (110000, 90000),
        (120000, 100000),
    ]


async def test_savers_hear_when_one_unit_is_left(app: Any, seller: Any, buyer: Any) -> None:
    item = await create_listing(seller, stock=3)
    async with make_client(app) as watcher:
        await register(watcher, display_name="Watcher")
        await watcher.put(f"/api/listings/{item['id']}/save")
        await buyer.put(f"/api/listings/{item['id']}/save")
        response = await buyer.post(
            "/api/checkouts",
            json={"provider": "fake", "items": [{"listing_id": item["id"], "quantity": 2}]},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        assert response.status_code == 201, response.text
        assert len(await notifications(watcher, "saved_low_stock")) == 1
        # The buyer who took the units is not told that one is left.
        assert await notifications(buyer, "saved_low_stock") == []


# --- feed ------------------------------------------------------------------------------------


async def test_following_tab_lists_new_listings_of_followed_sellers_page_by_page(
    app: Any, seller: Any, buyer: Any
) -> None:
    ids = [(await create_listing(seller, title=f"Anúncio seguido {n}"))["id"] for n in range(5)]
    async with make_client(app) as other:
        await register(other, display_name="Other seller")
        await create_listing(other, title="Anúncio de quem não sigo")
    assert (await feed_ids(buyer, "following"))[0] == []

    await buyer.put(f"/api/users/{(await me(seller))['id']}/follow")
    seen: list[str] = []
    cursor = None
    while True:
        page, cursor = await feed_ids(
            buyer, "following", limit=2, **({"cursor": cursor} if cursor else {})
        )
        seen += page
        if not cursor:
            break
    assert seen == list(reversed(ids))


async def test_following_tab_needs_an_account(app: Any) -> None:
    async with make_client(app) as anon:
        response = await anon.get("/api/feed", params={"tab": "following"})
    assert response.json()["error"]["code"] == "sign_in_required"


async def test_trending_ranks_saves_and_keeps_pages_stable(
    app: Any, seller: Any, buyer: Any
) -> None:
    quiet = [(await create_listing(seller, title=f"Anúncio calmo {n}"))["id"] for n in range(3)]
    hot = (await create_listing(seller, title="Anúncio em alta"))["id"]
    await buyer.put(f"/api/listings/{hot}/save")

    first, cursor = await feed_ids(buyer, "trending", limit=2)
    assert first[0] == hot
    # A save that lands between pages does not reshuffle the next page.
    await buyer.put(f"/api/listings/{quiet[0]}/save")
    second, _ = await feed_ids(buyer, "trending", limit=2, cursor=cursor)
    assert sorted(first + second) == sorted([*quiet, hot])


async def test_nearby_tab_uses_the_viewers_city(app: Any, seller: Any, buyer: Any) -> None:
    near = await create_listing(seller, city="Olinda", state="PE")
    far = await create_listing(seller, city="Porto Alegre", state="RS")
    ids, _ = await feed_ids(buyer, "nearby", radius_km=50)  # buyer lives in Recife
    assert near["id"] in ids
    assert far["id"] not in ids
    async with make_client(app) as anon:
        homeless = await anon.get("/api/feed", params={"tab": "nearby"})
        assert homeless.json()["error"]["code"] == "city_required"


# --- blocks ----------------------------------------------------------------------------------


async def test_blocking_hides_content_in_both_directions(app: Any, seller: Any, buyer: Any) -> None:
    seller_id, buyer_id = (await me(seller))["id"], (await me(buyer))["id"]
    sellers_item = await create_listing(seller)
    await buyer.put(f"/api/users/{seller_id}/follow")
    await seller.put(f"/api/users/{buyer_id}/follow")
    await buyer.put(f"/api/listings/{sellers_item['id']}/save")

    assert (await seller.put(f"/api/users/{buyer_id}/block")).status_code == 204

    # The blocked buyer no longer finds the seller anywhere.
    assert (await buyer.get(f"/api/listings/{sellers_item['id']}")).status_code == 404
    assert (await buyer.get("/api/listings", params={"q": "bicicleta"})).json()["items"] == []
    assert (await feed_ids(buyer, "trending"))[0] == []
    assert (await buyer.get(f"/api/users/{seller_id}")).status_code == 404
    checkout = await buyer.post(
        "/api/checkouts",
        json={"provider": "fake", "items": [{"listing_id": sellers_item["id"], "quantity": 1}]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert checkout.status_code == 404
    assert (await buyer.put(f"/api/users/{seller_id}/follow")).json()["error"]["code"] == "blocked"

    # Ties were cut both ways and the counters follow.
    assert (await buyer.get("/api/me/saved")).json()["items"] == []
    seller_profile = (await seller.get(f"/api/users/{seller_id}")).json()
    assert seller_profile["follower_count"] == seller_profile["following_count"] == 0
    assert (await seller.get(f"/api/listings/{sellers_item['id']}")).json()["save_count"] == 0

    # The blocker no longer sees the buyer's listings, but can still open the profile to unblock.
    buyers_item = await create_listing(buyer, title="Anúncio do comprador")
    assert (await seller.get(f"/api/listings/{buyers_item['id']}")).status_code == 404
    profile = (await seller.get(f"/api/users/{buyer_id}")).json()
    assert profile["blocked"] is True
    assert [u["id"] for u in (await seller.get("/api/me/blocks")).json()] == [buyer_id]

    assert (await seller.delete(f"/api/users/{buyer_id}/block")).status_code == 204
    assert (await buyer.get(f"/api/listings/{sellers_item['id']}")).status_code == 200


# --- reports ---------------------------------------------------------------------------------


async def test_reports_are_deduplicated_and_moderated_by_admins(
    app: Any, seller: Any, buyer: Any
) -> None:
    item = await create_listing(seller)
    body = {"target_type": "listing", "target_id": item["id"], "reason": "scam", "details": "x"}
    first = await buyer.post("/api/reports", json=body)
    again = await buyer.post("/api/reports", json=body)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    async with make_client(app) as other:
        await register(other, display_name="Another reporter")
        assert (await other.post("/api/reports", json=body)).status_code == 201

        assert (await other.get("/api/admin/reports")).status_code == 403
        await make_admin(app, other)
        queue = (await other.get("/api/admin/reports")).json()
        assert len(queue) == 2
        assert queue[0]["reports_on_target"] == 2
        assert queue[0]["target_label"] == item["title"]

        resolved = await other.post(
            f"/api/admin/reports/{queue[0]['id']}/resolve", json={"outcome": "actioned"}
        )
        assert resolved.json() == {"closed": 2}
        assert (await other.get("/api/admin/reports")).json() == []
    assert (await buyer.get(f"/api/listings/{item['id']}")).status_code == 404
    assert (await notifications(seller, "listing_removed"))[0]["listing_id"] == item["id"]


async def test_reporting_yourself_is_rejected(seller: Any) -> None:
    my_id = (await me(seller))["id"]
    response = await seller.post(
        "/api/reports", json={"target_type": "user", "target_id": my_id, "reason": "spam"}
    )
    assert response.json()["error"]["code"] == "report_self"


# --- profile ---------------------------------------------------------------------------------


async def test_profile_shows_bio_avatar_and_active_listings(app: Any, seller: Any) -> None:
    seller_id = (await me(seller))["id"]
    await seller.patch("/api/auth/me", json={"bio": "Bikes revisadas em Recife."})
    upload = await seller.post(
        "/api/me/avatar", files={"file": ("me.png", image_bytes(size=(600, 600)), "image/png")}
    )
    assert upload.status_code == 200, upload.text
    item = await create_listing(seller)
    await create_listing(seller, publish=False, title="Rascunho escondido")

    async with make_client(app) as anon:
        profile = (await anon.get(f"/api/users/{seller_id}")).json()
        listings = (await anon.get(f"/api/users/{seller_id}/listings")).json()["items"]
    assert profile["bio"] == "Bikes revisadas em Recife."
    assert profile["avatar_url"]
    assert profile["avatar_url"].endswith(".webp")
    assert profile["listing_count"] == 1
    assert profile["is_me"] is False
    assert [entry["id"] for entry in listings] == [item["id"]]
    assert (await me(seller))["avatar_url"] == profile["avatar_url"]
    assert (await seller.get(f"/api/listings/{item['id']}")).json()["seller"]["avatar_url"]

    assert (await seller.delete("/api/me/avatar")).status_code == 204
    assert (await me(seller))["avatar_url"] is None
    await seller.patch("/api/auth/me", json={"bio": ""})
    assert (await me(seller))["bio"] is None


async def test_listing_share_image_is_a_jpeg_for_link_previews(app: Any, seller: Any) -> None:
    item = await create_listing(seller)
    async with make_client(app) as anon:
        response = await anon.get(f"/api/listings/{item['id']}/og.jpg")
        draft = await create_listing(seller, publish=False)
        hidden = await anon.get(f"/api/listings/{draft['id']}/og.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.content.startswith(b"\xff\xd8\xff")
    assert hidden.status_code == 404
