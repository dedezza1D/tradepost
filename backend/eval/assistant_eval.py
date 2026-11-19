"""Listing assistant evaluation: drafts and price suggestions for the photo set.

Usage (needs the search evaluation database and a local Ollama with the vision and embedding
models; run ``eval.search_eval`` once first so the catalog is loaded and embedded):

    uv run python -m eval.listing_photos     # fetch the photos listed in listing_photos.json
    uv run python -m eval.assistant_eval               # both prompts; resumes a cut-short run
    uv run python -m eval.assistant_eval --arm labels  # one prompt at a time on a small machine

Every photo shows one product of a kind in ``search_dataset.py``, which gives the expected
category and price range. The draft runs through the same code as the API (prompt, schema,
clean-up); the price suggestion runs against the evaluation catalog.

Metrics:

- category: the draft's category is the expected one;
- product named: the title names the kind of product (a word list per kind);
- title rubric, 0 to 4: names the product, 15 to 60 characters, no claim a photo cannot
  support, written in Portuguese (no common English words);
- raw claims: share of descriptions in which the model claimed something the photo cannot show
  (condition, working order); the clean-up removes those sentences before the form sees them;
- price: the suggested median lies within the price range of that kind in the catalog, and the
  error of the median against the middle of that range.

Three prompts are compared (see ARMS): the one in use, the first version (English category
slugs) and one without the guide to what each category holds.
"""

import argparse
import asyncio
import json
import re
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from eval.listing_photos import MANIFEST, PHOTOS, SEARCHES
from eval.search_dataset import catalog
from eval.search_eval import eval_database_url
from tradepost import assistant
from tradepost.ai.client import AI, OllamaProvider
from tradepost.ai.models import AICall
from tradepost.config import get_settings
from tradepost.db import Database
from tradepost.media.images import model_input

HERE = Path(__file__).parent
CACHE = HERE / ".cache"

NAMES: dict[str, str] = {
    "bicicleta": r"bicicleta|bike|magrela",
    "celular": r"celular|smartphone|iphone|galaxy|telefone",
    "notebook": r"notebook|laptop|macbook|thinkpad|computador port",
    "geladeira": r"geladeira|refrigerador",
    "sofa": r"sof[aá]",
    "mesa": r"mesa",
    "cadeira": r"cadeira|poltrona",
    "tenis": r"t[eê]nis",
    "violao": r"viol[aã]o",
    "guitarra": r"guitarra",
    "livro": r"livro",
    "console": r"console|videogame|video game|nintendo|playstation|xbox|game ?boy|mega drive|"
    r"switch|sega",
    "camera": r"c[aâ]mera|m[aá]quina fotogr",
    "fone": r"fone|headphone",
    "tv": r"\btv\b|televis[aã]o|televisor",
    "carrinho_bebe": r"carrinho",
    "air_fryer": r"air ?fryer|fritadeira",
    "liquidificador": r"liquidificador",
}
CATEGORY_OF = {kind: category for kind, (category, _) in SEARCHES.items()}
ENGLISH = re.compile(r"\b(the|with|and|black|white|used|new|for|of)\b", re.IGNORECASE)


@dataclass
class Result:
    file: str
    kind: str
    category_ok: bool
    named: bool
    rubric: int
    raw_claims: bool
    seconds: float
    tokens_in: int
    tokens_out: int
    title: str
    category: str | None
    retried: bool = False
    price_ok: bool | None = None
    price_error: float | None = None


def rubric(kind: str, title: str) -> tuple[bool, int]:
    named = bool(re.search(NAMES[kind], title, re.IGNORECASE))
    points = int(named)
    points += int(15 <= len(title) <= 60)
    points += int(not assistant.UNSUPPORTED.search(title))
    points += int(not ENGLISH.search(title))
    return named, points


ARMS: dict[str, tuple[str, assistant.DraftPrompt]] = {
    "labels": ("Portuguese category names, with guide (used)", assistant.PROMPT),
    "slugs": (
        "English category slugs, with guide (first version)",
        assistant.DraftPrompt(labels=False),
    ),
    "noguide": ("Portuguese category names, no guide", assistant.DraftPrompt(guide=False)),
}
USED = "labels"


def checkpoint_path(arm: str) -> Path:
    return CACHE / f"assistant-{arm}.jsonl"


def load(path: Path) -> dict[str, Result]:
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {row["file"]: Result(**row) for row in rows}


async def run(arm: str, limit: int | None) -> list[Result]:
    settings = get_settings().model_copy(
        update={"ai_provider": "ollama", "database_url": eval_database_url()}
    )
    database = Database(settings)
    ai = AI(settings, database, OllamaProvider(settings.ai_base_url))
    entries: list[dict[str, Any]] = json.loads(MANIFEST.read_text(encoding="utf-8"))[:limit]
    ranges: dict[str, tuple[int, int]] = {}
    for item in catalog():
        low, high = ranges.get(item.kind, (item.price_cents, item.price_cents))
        ranges[item.kind] = (min(low, item.price_cents), max(high, item.price_cents))
    prompt = ARMS[arm][1]
    checkpoint = checkpoint_path(arm)
    done = load(checkpoint)
    results: list[Result] = []
    for entry in entries:
        if entry["file"] in done:
            results.append(done[entry["file"]])
            continue
        photo = await asyncio.to_thread((PHOTOS / entry["file"]).read_bytes)
        image = model_input(
            photo, max_bytes=settings.max_photo_bytes, max_pixels=settings.max_image_pixels
        )
        # The same path as the API (prompt, schema, retry, clean-up), without the answer cache.
        started = time.perf_counter()
        async with database.sessionmaker() as db, db.begin():
            draft = await assistant.draft_from_photos(db, ai, [image], prompt=prompt, cache=False)
        seconds = time.perf_counter() - started
        async with database.sessionmaker() as db:
            calls = (
                await db.execute(
                    select(AICall.ok, AICall.input_tokens, AICall.output_tokens)
                    .where(AICall.feature == assistant.FEATURE_DRAFT)
                    .order_by(AICall.id.desc())
                    .limit(2)
                )
            ).all()
        named, points = rubric(entry["kind"], draft.title)
        result = Result(
            file=entry["file"],
            kind=entry["kind"],
            category_ok=draft.category_slug == entry["category"],
            named=named,
            rubric=points,
            raw_claims=draft.removed_claims > 0,
            seconds=seconds,
            tokens_in=calls[0].input_tokens,
            tokens_out=calls[0].output_tokens,
            title=draft.title,
            category=draft.category_slug,
            # The answer came on the second try (with a repeat penalty) when the call before failed.
            retried=len(calls) == 2 and not calls[1].ok,
        )
        if arm == USED:
            async with database.sessionmaker() as db:
                price = await assistant.suggest_price(
                    db,
                    ai,
                    title=draft.title,
                    description=draft.description,
                    category=draft.category_slug,
                )
            if price is not None:
                low, high = ranges[entry["kind"]]
                middle = (low + high) / 2
                result.price_ok = low <= price.median_cents <= high
                result.price_error = abs(price.median_cents - middle) / middle
        results.append(result)
        # One line per photo: a run cut short resumes where it stopped.
        with checkpoint.open("a", encoding="utf-8") as out:
            out.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
        print(
            f"{entry['file']:<22} {seconds:5.1f}s {draft.category_slug!s:<12} {draft.title}",
            flush=True,
        )
    await database.dispose()
    return results


def share(values: list[bool]) -> str:
    return f"{100 * sum(values) / len(values):.0f}%" if values else "-"


def report(arms: dict[str, list[Result]]) -> str:
    def row(name: str, rs: list[Result]) -> str:
        return (
            f"| {name} | {share([r.category_ok for r in rs])} | {share([r.named for r in rs])} |"
            f" {statistics.fmean(r.rubric for r in rs):.2f} / 4 |"
            f" {share([r.rubric == 4 for r in rs])} | {share([r.raw_claims for r in rs])} |"
        )

    used = arms[USED]
    priced = [r for r in used if r.price_ok is not None]
    seconds = sorted(r.seconds for r in used[1:])  # the first call loads the model
    lines = [
        "# Listing assistant evaluation",
        "",
        f"{len(used)} photos of {len({r.kind for r in used})} kinds of product from Wikimedia"
        " Commons (sources and licenses in `eval/listing_photos.json`). Model:"
        f" {get_settings().ai_chat_model} on a local Ollama server.",
        "",
        "Generated by `uv run python -m eval.assistant_eval` (see the module docstring).",
        "",
        "## Drafts",
        "",
        "| Prompt | Category | Product named in title | Title rubric | Full marks |"
        " Raw unsupported claims |",
        "|---|---|---|---|---|---|",
    ]
    lines += [row(label, arms[arm]) for arm, (label, _) in ARMS.items() if arms.get(arm)]
    lines += [
        "",
        "The first version offered the category slugs in English and filed every camera and"
        " headphone under `phones`. The version in use names the categories in Portuguese; it was"
        " written after seeing those mistakes on this same set, so its number is optimistic.",
        "",
        "Raw unsupported claims: the model's description claimed condition or working order the"
        " photo cannot show. Those sentences are removed before the draft reaches the form.",
        "",
        "Drafts that needed the second try with a repeat penalty (the first answer looped and was"
        f" aborted): {sum(r.retried for r in used)} of {len(used)}.",
        "",
        "## Price suggestion",
        "",
        f"- Suggestion given for {len(priced)} of {len(used)} drafts.",
        f"- Median within the kind's price range: {share([bool(r.price_ok) for r in priced])}.",
        "- Median error against the middle of that range:"
        f" {statistics.median(r.price_error or 0 for r in priced) * 100:.0f}%."
        if priced
        else "- No suggestions.",
        "",
        "## Cost and latency",
        "",
        f"- Draft latency: median {statistics.median(seconds):.1f} s, p90"
        f" {seconds[int(0.9 * (len(seconds) - 1))]:.1f} s (laptop GPU, one photo, model loaded).",
        f"- Tokens per draft: {statistics.fmean(r.tokens_in for r in used):.0f} in (mostly the"
        f" image), {statistics.fmean(r.tokens_out for r in used):.0f} out. A local model costs"
        " nothing per call.",
        "",
        "## Category mistakes of the version in use",
        "",
        "| Photo | Expected | Answered | Title |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {r.file} | {CATEGORY_OF[r.kind]} | {r.category} | {r.title} |"
        for r in used
        if not r.category_ok
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--arm",
        choices=[*ARMS, "all"],
        default="all",
        help="run one prompt at a time on a small machine; the report uses whatever was run",
    )
    parser.add_argument("--fresh", action="store_true", help="discard saved per-photo results")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "assistant.md")
    args = parser.parse_args()
    chosen = list(ARMS) if args.arm == "all" else [args.arm]
    for arm in chosen:
        if args.fresh:
            checkpoint_path(arm).unlink(missing_ok=True)
        asyncio.run(run(arm, args.limit))
    arms = {arm: list(load(checkpoint_path(arm)).values()) for arm in ARMS}
    if not arms[USED]:
        raise SystemExit(f"No results for the prompt in use yet: run with --arm {USED}.")
    text = report(arms)
    args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
