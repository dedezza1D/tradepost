"""Photo set for evaluating the listing assistant, fetched from Wikimedia Commons.

Usage:
    uv run python -m eval.listing_photos               # fetch the manifest's photos (eval/.cache)
    uv run python -m eval.listing_photos --candidates  # search Commons for new candidates

Each photo is a product of one of the kinds in ``search_dataset.py``, so the expected category
and price range come from there. The manifest (``eval/listing_photos.json``) records the source
page, author and license of every file; the images themselves are not committed, only fetched.
Only freely licensed files are considered (public domain, CC0, CC BY, CC BY-SA).

Search results include scenes, diagrams and unrelated pictures, so the manifest is curated by
hand from the candidates: it keeps photos that show one item the way a seller would photograph
it, and drops photos of people.
"""

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).parent
MANIFEST = HERE / "listing_photos.json"
PHOTOS = HERE / ".cache" / "photos"
CANDIDATES = HERE / ".cache" / "candidates"
API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks automated clients to identify themselves.
USER_AGENT = "tradepost-eval/1.0 (portfolio project; listing assistant evaluation)"
PER_KIND = 12
WIDTH = 800

# Kind -> (category slug, Commons search). Searches name the product the way a photo of one for
# sale would show it: a single item, not a scene.
SEARCHES: dict[str, tuple[str, str]] = {
    "bicicleta": ("bikes", "bicycle isolated"),
    "celular": ("phones", "smartphone isolated"),
    "notebook": ("computers", "laptop isolated"),
    "geladeira": ("appliances", "refrigerator"),
    "sofa": ("furniture", "sofa furniture isolated"),
    "mesa": ("furniture", "wooden table furniture"),
    "cadeira": ("furniture", "office chair isolated"),
    "tenis": ("shoes", "sneaker shoe"),
    "violao": ("music", "classical guitar"),
    "guitarra": ("music", "electric guitar"),
    "livro": ("books", "book cover"),
    "console": ("games", "video game console"),
    "camera": ("electronics", "digital camera"),
    "fone": ("electronics", "headphones isolated"),
    "tv": ("electronics", "flat screen television"),
    "carrinho_bebe": ("baby", "pram baby carriage"),
    "air_fryer": ("home", "air fryer appliance"),
    "liquidificador": ("home", "blender appliance"),
}
FREE = re.compile(r"^(public domain|pd|cc0|cc by(-sa)?( \d\.\d)?)", re.IGNORECASE)


async def polite_get(client: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
    """GET with a pause before each request and backoff when the server asks to slow down."""
    for attempt in range(6):
        await asyncio.sleep(1.5)
        response = await client.get(url, **kwargs)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        await asyncio.sleep(float(response.headers.get("retry-after", 10 * (attempt + 1))))
    response.raise_for_status()
    return response


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


async def search(client: httpx.AsyncClient, kind: str, query: str) -> list[dict[str, Any]]:
    response = await polite_get(
        client,
        API,
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}",
            "gsrnamespace": 6,
            "gsrlimit": 40,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|mime|size",
            "iiurlwidth": WIDTH,
        },
    )
    pages = sorted(
        response.json().get("query", {}).get("pages", {}).values(), key=lambda p: p["index"]
    )
    out: list[dict[str, Any]] = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        license_name = meta.get("LicenseShortName", {}).get("value", "")
        if info.get("mime") != "image/jpeg" or not FREE.match(license_name):
            continue
        if info.get("width", 0) < 600 or info.get("height", 0) < 400:
            continue
        out.append(
            {
                "file": f"{kind}-{len(out) + 1:02d}.jpg",
                "kind": kind,
                "category": SEARCHES[kind][0],
                "title": page["title"],
                "source": info["descriptionurl"],
                "image": info["thumburl"],
                "author": _plain(meta.get("Artist", {}).get("value", "unknown"))[:200],
                "license": license_name,
            }
        )
        if len(out) == PER_KIND:
            break
    return out


async def refresh() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        entries: list[dict[str, Any]] = []
        for kind, (_, query) in SEARCHES.items():
            entries += await search(client, kind, query)
    return entries


async def fetch(entries: list[dict[str, Any]], folder: Path) -> int:
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    fetched = 0
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        for entry in entries:
            target = folder / entry["file"]
            if target.exists():
                continue
            response = await polite_get(client, entry["image"], follow_redirects=True)
            if not response.headers.get("content-type", "").startswith("image/jpeg"):
                raise ValueError(f"{entry['file']}: not a JPEG")
            await asyncio.to_thread(target.write_bytes, response.content)
            fetched += 1
    return fetched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--candidates", action="store_true")
    args = parser.parse_args()
    if args.candidates:
        entries = asyncio.run(refresh())
        text = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
        (CANDIDATES.parent / "candidates.json").write_text(text, encoding="utf-8")
        print(f"{len(entries)} candidates, {asyncio.run(fetch(entries, CANDIDATES))} fetched")
        return
    entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
    print(f"{len(entries)} photos in the manifest, {asyncio.run(fetch(entries, PHOTOS))} fetched")


if __name__ == "__main__":
    main()
