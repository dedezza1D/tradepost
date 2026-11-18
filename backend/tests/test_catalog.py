import io
from typing import Any

from PIL import Image

from tests.conftest import LISTING, create_listing, image_bytes, make_client, register


async def test_publish_requires_a_photo(seller: Any) -> None:
    draft = await create_listing(seller, publish=False)
    assert draft["status"] == "draft"
    response = await seller.post(f"/api/listings/{draft['id']}/publish")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "photo_required"


async def test_published_listing_is_public_and_drafts_are_not(seller: Any, buyer: Any) -> None:
    listing = await create_listing(seller)
    draft = await create_listing(seller, publish=False, title="Rascunho escondido")

    public = await buyer.get(f"/api/listings/{listing['id']}")
    assert public.status_code == 200
    assert public.json()["photos"][0]["url"].endswith(".webp")

    assert (await buyer.get(f"/api/listings/{draft['id']}")).status_code == 404
    assert (await seller.get(f"/api/listings/{draft['id']}")).status_code == 200


async def test_other_users_cannot_change_a_listing(seller: Any, buyer: Any) -> None:
    listing = await create_listing(seller)
    lid = listing["id"]
    edit = await buyer.patch(f"/api/listings/{lid}", json={"version": 1, "price_cents": 1})
    pause = await buyer.post(f"/api/listings/{lid}/pause")
    delete = await buyer.delete(f"/api/listings/{lid}")
    upload = await buyer.post(
        f"/api/listings/{lid}/photos", files={"file": ("p.png", image_bytes(), "image/png")}
    )
    assert [r.status_code for r in (edit, pause, delete, upload)] == [404] * 4


async def test_stale_edit_is_rejected(seller: Any) -> None:
    listing = await create_listing(seller)
    lid, version = listing["id"], listing["version"]
    first = await seller.patch(f"/api/listings/{lid}", json={"version": version, "price_cents": 99})
    assert first.status_code == 200
    assert first.json()["version"] == version + 1
    stale = await seller.patch(f"/api/listings/{lid}", json={"version": version, "stock": 5})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"


async def test_stock_zero_marks_sold_out_and_restock_reactivates(seller: Any) -> None:
    listing = await create_listing(seller)
    lid = listing["id"]
    out = await seller.patch(
        f"/api/listings/{lid}", json={"version": listing["version"], "stock": 0}
    )
    assert out.json()["status"] == "sold_out"
    back = await seller.patch(
        f"/api/listings/{lid}", json={"version": out.json()["version"], "stock": 3}
    )
    assert back.json()["status"] == "active"


async def test_photo_is_reencoded_without_metadata(seller: Any, tmp_path: Any) -> None:
    draft = await create_listing(seller, publish=False)
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    exif[0x8825] = {2: (8.0, 3.0, 0.0)}  # GPS latitude
    jpeg = image_bytes("JPEG", (800, 600), exif=exif.tobytes())
    response = await seller.post(
        f"/api/listings/{draft['id']}/photos", files={"file": ("p.jpg", jpeg, "image/jpeg")}
    )
    assert response.status_code == 201, response.text
    photo = response.json()
    assert (photo["width"], photo["height"]) == (800, 600)

    stored = await seller.get(photo["url"])
    assert stored.status_code == 200
    with Image.open(io.BytesIO(stored.content)) as image:
        assert image.format == "WEBP"
        assert not image.getexif()
        assert "exif" not in image.info


async def test_non_images_and_disguised_files_are_rejected(seller: Any) -> None:
    draft = await create_listing(seller, publish=False)
    url = f"/api/listings/{draft['id']}/photos"
    script = await seller.post(url, files={"file": ("x.jpg", b"<?php echo 1; ?>", "image/jpeg")})
    svg = await seller.post(url, files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")})
    truncated = await seller.post(url, files={"file": ("x.png", image_bytes()[:40], "image/png")})
    for response in (script, svg, truncated):
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_image"


async def test_oversized_uploads_are_rejected(seller: Any) -> None:
    draft = await create_listing(seller, publish=False)
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024 + 10)
    response = await seller.post(
        f"/api/listings/{draft['id']}/photos", files={"file": ("big.png", big, "image/png")}
    )
    assert response.status_code == 400


async def test_photo_limit_per_listing(seller: Any) -> None:
    draft = await create_listing(seller, publish=False)
    url = f"/api/listings/{draft['id']}/photos"
    codes = [
        (await seller.post(url, files={"file": ("p.png", image_bytes(), "image/png")})).status_code
        for _ in range(9)
    ]
    assert codes == [201] * 8 + [400]


async def test_search_ignores_accents_and_ranks_matches(seller: Any, client: Any) -> None:
    await create_listing(seller, title="Câmera fotográfica Canon", category_slug="electronics")
    await create_listing(seller, title="Bicicleta aro 26 infantil")
    response = await client.get("/api/listings", params={"q": "camera fotografica"})
    titles = [item["title"] for item in response.json()["items"]]
    assert titles == ["Câmera fotográfica Canon"]


async def test_search_tolerates_a_typo_in_the_title(seller: Any, client: Any) -> None:
    await create_listing(seller, title="Guitarra Fender Stratocaster", category_slug="music")
    response = await client.get("/api/listings", params={"q": "stratocastr"})
    assert [i["title"] for i in response.json()["items"]] == ["Guitarra Fender Stratocaster"]


async def test_filters_and_keyset_pagination_by_price(seller: Any, client: Any) -> None:
    prices = [5000, 1000, 3000, 2000, 4000]
    for i, price in enumerate(prices):
        await create_listing(
            seller, title=f"Livro usado numero {i}", price_cents=price, category_slug="books"
        )
    await create_listing(seller, title="Sofá de três lugares", category_slug="furniture")

    seen: list[int] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"category": "books", "sort": "price_asc", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get("/api/listings", params=params)).json()
        seen += [item["price_cents"] for item in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == sorted(prices)

    ranged = await client.get(
        "/api/listings", params={"category": "books", "min_price": 2000, "max_price": 4000}
    )
    assert sorted(i["price_cents"] for i in ranged.json()["items"]) == [2000, 3000, 4000]


async def test_paused_and_removed_listings_leave_search(seller: Any, client: Any) -> None:
    listing = await create_listing(seller)
    assert len((await client.get("/api/listings")).json()["items"]) == 1
    await seller.post(f"/api/listings/{listing['id']}/pause")
    assert (await client.get("/api/listings")).json()["items"] == []


async def test_invalid_cursor_is_a_client_error(client: Any) -> None:
    response = await client.get(
        "/api/listings", params={"cursor": "not-a-cursor!!", "sort": "newest"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"


async def test_listing_requires_known_category_and_state(app: Any) -> None:
    async with make_client(app) as c:
        await register(c)
        unknown = await c.post("/api/listings", json={**LISTING, "category_slug": "nope"})
        bad_state = await c.post("/api/listings", json={**LISTING, "state": "ZZ"})
    assert unknown.status_code == 400
    assert bad_state.status_code == 422


async def test_uploads_stop_at_the_storage_quota(seller: Any) -> None:
    from tradepost.config import get_settings

    settings = get_settings()
    draft = await create_listing(seller, publish=False)
    url = f"/api/listings/{draft['id']}/photos"
    first = await seller.post(url, files={"file": ("p.png", image_bytes(), "image/png")})
    assert first.status_code == 201
    previous = settings.media_quota_bytes
    settings.media_quota_bytes = 1  # already used more than this
    try:
        full = await seller.post(url, files={"file": ("p.png", image_bytes(), "image/png")})
    finally:
        settings.media_quota_bytes = previous
    assert full.status_code == 507
    assert full.json()["error"]["code"] == "storage_full"


async def test_photo_limit_per_account(seller: Any) -> None:
    from tradepost.config import get_settings

    settings = get_settings()
    previous = settings.max_photos_per_user
    settings.max_photos_per_user = 2
    try:
        a = await create_listing(seller, publish=False)
        b = await create_listing(seller, publish=False)
        files = {"file": ("p.png", image_bytes(), "image/png")}
        codes = [
            (await seller.post(f"/api/listings/{a['id']}/photos", files=files)).status_code,
            (await seller.post(f"/api/listings/{b['id']}/photos", files=files)).status_code,
        ]
        third = await seller.post(f"/api/listings/{b['id']}/photos", files=files)
    finally:
        settings.max_photos_per_user = previous
    assert codes == [201, 201]
    assert third.json()["error"]["code"] == "photo_limit_reached"


async def test_photos_can_be_reordered_by_the_owner_only(seller: Any, buyer: Any) -> None:
    draft = await create_listing(seller, publish=False)
    url = f"/api/listings/{draft['id']}/photos"
    ids = [
        (await seller.post(url, files={"file": ("p.png", image_bytes(), "image/png")})).json()["id"]
        for _ in range(3)
    ]
    reordered = await seller.put(f"{url}/order", json={"photo_ids": [ids[2], ids[0], ids[1]]})
    assert reordered.status_code == 200
    assert [p["id"] for p in reordered.json()["photos"]] == [ids[2], ids[0], ids[1]]
    partial = await seller.put(f"{url}/order", json={"photo_ids": ids[:2]})
    assert partial.json()["error"]["code"] == "bad_photo_order"
    other = await buyer.put(f"{url}/order", json={"photo_ids": ids})
    assert other.status_code == 404
