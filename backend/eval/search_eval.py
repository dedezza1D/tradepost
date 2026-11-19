"""Search evaluation: full-text, vector and hybrid ranking on the judged set in search_dataset.py.

Usage (needs PostgreSQL with pgvector and a local Ollama with the embedding models pulled):

    uv run python -m eval.search_eval
    uv run python -m eval.search_eval --models bge-m3 --out eval/results/search.md

The catalog is loaded into a separate database (``tradepost_eval`` next to the one in
TP_DATABASE_URL, migrated to head), embedded with each model and searched with the same code the
API runs. The hybrid settings (RRF weights, k and the distance cutoff) are chosen on the dev
split; the report shows the test split, which the choice never saw. Embeddings are cached in
``eval/.cache`` (git-ignored), so re-runs only call the model for new or changed text.

Metrics, at 10 results (the first screen on a phone):

- nDCG@10: graded gain (2 for an exact match, 1 for the right kind of product) discounted by
  position, divided by the best possible ordering;
- Recall@10: share of the best matches (grade 2, or grade 1 when the catalog has no exact
  match) found in the first 10, out of at most 10;
- MRR: 1 / position of the first best match;
- no results: share of queries that returned nothing.
"""

import argparse
import asyncio
import hashlib
import itertools
import json
import math
import os
import statistics
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import Float, String, cast, literal, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from eval.search_dataset import OUT_OF_CATALOG, QUERIES, Item, Query, catalog
from tradepost.catalog.models import ItemCondition, Listing, ListingStatus
from tradepost.catalog.schemas import SearchParams
from tradepost.catalog.search import search_listings
from tradepost.catalog.semantic import (
    CANDIDATES,
    ListingEmbedding,
    Vector,
    as_literal,
    document_text,
    fuse,
    prefixed,
)
from tradepost.config import get_settings
from tradepost.identity.models import User
from tradepost.media.storage import build_storage

HERE = Path(__file__).parent
CACHE = HERE / ".cache"
AT = 10
NAMESPACE = uuid.UUID("6f1d8c3e-2b7a-4f0e-9a51-3c9e8d4b2a10")

TEXT_WEIGHTS = (0.25, 0.5, 1.0, 2.0)
RRF_KS = (10, 30, 60)
CUTOFFS = (0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 2.0)
# A looser cutoff barely moves nDCG but lets more unrelated listings through, so the chosen
# cutoff is the strictest one within this distance of the best dev nDCG.
TOLERANCE = 0.01


# --- embeddings ---------------------------------------------------------------------------------


class Embedder:
    """Ollama embeddings with a JSON cache per model, keyed by a hash of the exact input."""

    def __init__(self, base_url: str, model: str) -> None:
        self.model = model
        self.http = httpx.AsyncClient(base_url=base_url, timeout=300)
        self.path = CACHE / f"embeddings-{model.replace(':', '_')}.json"
        self.cache: dict[str, list[float]] = (
            json.loads(self.path.read_text()) if self.path.exists() else {}
        )

    async def embed(self, texts: Sequence[str], *, query: bool) -> list[list[float]]:
        inputs = prefixed(self.model, texts, query=query)
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in inputs]
        missing = [(k, t) for k, t in zip(keys, inputs, strict=True) if k not in self.cache]
        for start in range(0, len(missing), 32):
            batch = missing[start : start + 32]
            response = await self.http.post(
                "/api/embed", json={"model": self.model, "input": [t for _, t in batch]}
            )
            response.raise_for_status()
            for (k, _), vector in zip(batch, response.json()["embeddings"], strict=True):
                self.cache[k] = vector
        return [self.cache[k] for k in keys]

    async def time_query(self, query: str) -> float:
        began = time.perf_counter()
        response = await self.http.post(
            "/api/embed",
            json={"model": self.model, "input": prefixed(self.model, [query], query=True)},
        )
        response.raise_for_status()
        return (time.perf_counter() - began) * 1000

    def save(self) -> None:
        CACHE.mkdir(exist_ok=True)
        self.path.write_text(json.dumps(self.cache))


# --- database -----------------------------------------------------------------------------------


def eval_database_url() -> str:
    url = make_url(get_settings().database_url)
    return url.set(database="tradepost_eval").render_as_string(hide_password=False)


async def ensure_database(url: str) -> None:
    admin = create_async_engine(
        make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        exists = (
            await conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'tradepost_eval'"))
        ).first()
        if not exists:
            await conn.execute(text("CREATE DATABASE tradepost_eval"))
    await admin.dispose()
    env = {**os.environ, "TP_DATABASE_URL": url}
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=env,
        cwd=HERE.parent,
        capture_output=True,
    )


def listing_id(item: Item) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, item.key)


async def seed(db: AsyncSession, items: list[Item]) -> None:
    """Replace the eval catalog with the generated one (same ids every run)."""
    await db.execute(text("TRUNCATE listings, users CASCADE"))
    seller = User(
        email="seller@eval.invalid",
        password_hash="!",  # noqa: S106 - no login in the eval database
        display_name="Eval seller",
        city="Recife",
        state="PE",
    )
    db.add(seller)
    await db.flush()
    for item in items:
        db.add(
            Listing(
                id=listing_id(item),
                seller_id=seller.id,
                title=item.title,
                description=item.description,
                category_slug=item.category,
                condition=ItemCondition.USED,
                price_cents=item.price_cents,
                stock=1,
                city="Recife",
                state="PE",
                status=ListingStatus.ACTIVE,
            )
        )
    await db.flush()


async def index(db: AsyncSession, embedder: Embedder, items: list[Item]) -> None:
    texts = [document_text(i.title, i.description) for i in items]
    vectors = await embedder.embed(texts, query=False)
    for item, content, vector in zip(items, texts, vectors, strict=True):
        await db.execute(
            text(
                "INSERT INTO listing_embeddings (listing_id, model, content_hash, embedding)"
                " VALUES (:id, :model, :hash, CAST(CAST(:vec AS text) AS vector))"
            ),
            {
                "id": listing_id(item),
                "model": embedder.model,
                "hash": hashlib.sha256(content.encode()).hexdigest(),
                "vec": as_literal(vector),
            },
        )


# --- rankers ------------------------------------------------------------------------------------


async def text_ranking(db: AsyncSession, query: str) -> list[uuid.UUID]:
    params = SearchParams(q=query, limit=60)
    page = await search_listings(db, build_storage(get_settings()), params, None)
    return [card.id for card in page.items][:CANDIDATES]


async def vector_ranking(
    db: AsyncSession, model: str, vector: list[float]
) -> list[tuple[uuid.UUID, float]]:
    """Top candidates with their cosine distance (the cutoff is applied afterwards, which
    selects the same rows as filtering in SQL before the limit)."""
    dist = ListingEmbedding.embedding.op("<=>", return_type=Float())(
        cast(literal(as_literal(vector), String), Vector())
    )
    rows = await db.execute(
        select(ListingEmbedding.listing_id, dist)
        .where(ListingEmbedding.model == model)
        .order_by(dist)
        .limit(CANDIDATES)
    )
    return [(row[0], float(row[1])) for row in rows]


# --- metrics ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scores:
    ndcg: float
    recall: float
    mrr: float
    empty: float

    def row(self) -> str:
        return f"{self.ndcg:.3f} | {self.recall:.3f} | {self.mrr:.3f} | {self.empty * 100:.0f}%"


def score_query(query: Query, ranked: list[uuid.UUID], by_id: dict[uuid.UUID, Item]) -> Scores:
    grades = [query.grade(by_id[i]) for i in ranked[:AT]]
    ideal = sorted((query.grade(item) for item in by_id.values()), reverse=True)[:AT]

    def dcg(gs: Sequence[int]) -> float:
        return sum(float(2**g - 1) / math.log2(pos + 2) for pos, g in enumerate(gs))

    best_grade = ideal[0] if ideal else 0
    best_total = sum(1 for item in by_id.values() if query.grade(item) == best_grade)
    hits = sum(1 for g in grades if g == best_grade)
    first = next((pos for pos, g in enumerate(grades, start=1) if g == best_grade), None)
    return Scores(
        ndcg=dcg(grades) / dcg(ideal) if dcg(ideal) else 0.0,
        recall=hits / min(AT, best_total) if best_grade else 0.0,
        mrr=1 / first if first else 0.0,
        empty=0.0 if ranked else 1.0,
    )


def mean(scores: list[Scores]) -> Scores:
    return Scores(
        ndcg=statistics.fmean(s.ndcg for s in scores),
        recall=statistics.fmean(s.recall for s in scores),
        mrr=statistics.fmean(s.mrr for s in scores),
        empty=statistics.fmean(s.empty for s in scores),
    )


# --- run ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Hybrid:
    text_weight: float
    k: int
    cutoff: float

    def label(self) -> str:
        return f"text weight {self.text_weight:g}, k {self.k}, cutoff {self.cutoff:g}"


Ranker = Callable[[Query | str], list[uuid.UUID]]


def evaluate(ranker: Ranker, queries: list[Query], by_id: dict[uuid.UUID, Item]) -> Scores:
    return mean([score_query(q, ranker(q), by_id) for q in queries])


async def run(models: list[str]) -> tuple[list[str], dict[str, Hybrid]]:
    settings = get_settings()
    url = eval_database_url()
    await ensure_database(url)
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    items = catalog()
    by_id = {listing_id(i): i for i in items}
    dev = [q for q in QUERIES if q.split == "dev"]
    test = [q for q in QUERIES if q.split == "test"]

    embedders = {m: Embedder(settings.ai_base_url, m) for m in models}
    async with sessions() as db, db.begin():
        await seed(db, items)
        for embedder in embedders.values():
            await index(db, embedder, items)

    text_runs: dict[str, list[uuid.UUID]] = {}
    vector_runs: dict[tuple[str, str], list[tuple[uuid.UUID, float]]] = {}
    text_ms: list[float] = []
    vector_ms: list[float] = []
    async with sessions() as db:
        for q in QUERIES:
            began = time.perf_counter()
            text_runs[q.text] = await text_ranking(db, q.text)
            text_ms.append((time.perf_counter() - began) * 1000)
        for stray_text in OUT_OF_CATALOG:
            text_runs[stray_text] = await text_ranking(db, stray_text)
        texts = [q.text for q in QUERIES] + OUT_OF_CATALOG
        for model, embedder in embedders.items():
            vectors = await embedder.embed(texts, query=True)
            for query_text, vector in zip(texts, vectors, strict=True):
                began = time.perf_counter()
                vector_runs[(model, query_text)] = await vector_ranking(db, model, vector)
                vector_ms.append((time.perf_counter() - began) * 1000)
    embed_ms = {m: [await e.time_query(q.text) for q in QUERIES[:30]] for m, e in embedders.items()}
    for embedder in embedders.values():
        embedder.save()
    await engine.dispose()

    def text_only(q: Query | str) -> list[uuid.UUID]:
        return text_runs[q if isinstance(q, str) else q.text]

    def vector_only(model: str, cutoff: float) -> Ranker:
        return lambda q: [
            i for i, d in vector_runs[(model, q if isinstance(q, str) else q.text)] if d <= cutoff
        ]

    def hybrid(model: str, h: Hybrid) -> Ranker:
        return lambda q: fuse(
            [(text_only(q), h.text_weight), (vector_only(model, h.cutoff)(q), 1.0)], h.k
        )

    def stray(ranker: Ranker) -> str:
        """Out-of-catalog queries: share that returned anything, and mean results returned."""
        counts = [len(ranker(q)[:AT]) for q in OUT_OF_CATALOG]
        share = sum(1 for c in counts if c) / len(counts)
        return f"{share * 100:.0f}% ({statistics.fmean(counts):.1f})"

    lines = [
        "# Search evaluation",
        "",
        f"Catalog: {len(items)} generated listings in {len({i.kind for i in items})} product"
        f" kinds. Queries: {len(QUERIES)} ({len(dev)} dev, {len(test)} test).",
        "Settings below were chosen on the dev split by nDCG@10 (the cutoff: the strictest within"
        f" {TOLERANCE} of the best); the table reports the test split.",
        "",
        "Generated by `uv run python -m eval.search_eval` (see the module docstring).",
        "",
        "## Test split",
        "",
        "| Search | nDCG@10 | Recall@10 | MRR | No results | Out-of-catalog answered |",
        "|---|---|---|---|---|---|",
        f"| Full-text (live site) | {evaluate(text_only, test, by_id).row()} |"
        f" {stray(text_only)} |",
    ]
    chosen: dict[str, Hybrid] = {}
    for model in models:
        by_cut = {c: evaluate(vector_only(model, c), dev, by_id).ndcg for c in CUTOFFS}
        best_cut = min(c for c in CUTOFFS if by_cut[c] >= max(by_cut.values()) - TOLERANCE)
        lines.append(
            f"| Vector only, {model} (cutoff {best_cut:g}) |"
            f" {evaluate(vector_only(model, best_cut), test, by_id).row()} |"
            f" {stray(vector_only(model, best_cut))} |"
        )
        grid = [Hybrid(w, k, c) for w, k, c in itertools.product(TEXT_WEIGHTS, RRF_KS, CUTOFFS)]
        dev_ndcg = {h: evaluate(hybrid(model, h), dev, by_id).ndcg for h in grid}
        top = max(grid, key=lambda h: dev_ndcg[h])
        best = min(
            (
                h
                for h in grid
                if (h.text_weight, h.k) == (top.text_weight, top.k)
                and dev_ndcg[h] >= dev_ndcg[top] - TOLERANCE
            ),
            key=lambda h: h.cutoff,
        )
        chosen[model] = best
        lines.append(
            f"| Hybrid RRF, {model} ({best.label()}) |"
            f" {evaluate(hybrid(model, best), test, by_id).row()} |"
            f" {stray(hybrid(model, best))} |"
        )
    lines += [
        "",
        f"Out-of-catalog answered: of {len(OUT_OF_CATALOG)} queries for things the catalog does"
        " not sell, the share that returned any listing (mean listings returned, at most 10).",
        "",
        "Distance alone cannot say that the catalog does not sell something: a cutoff strict"
        " enough to silence those queries also drops real matches (table below). The API marks"
        " a page whose results matched no word of the query (`approximate`), and the app"
        " presents them as similar items instead of matches.",
    ]

    lines += ["", "## Distance cutoff (dev split)", ""]
    lines += ["| Model | Cutoff | Hybrid nDCG@10 | Out-of-catalog answered |", "|---|---|---|---|"]
    for model in models:
        for cutoff in CUTOFFS:
            h = Hybrid(chosen[model].text_weight, chosen[model].k, cutoff)
            lines.append(
                f"| {model} | {cutoff:g} | {evaluate(hybrid(model, h), dev, by_id).ndcg:.3f} |"
                f" {stray(hybrid(model, h))} |"
            )

    lines += ["", "## Per query kind (test split, nDCG@10)", ""]
    header = ["Query", "Full-text"] + [f"Hybrid {m}" for m in models]
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for q in test:
        cells = [q.text, f"{score_query(q, text_only(q), by_id).ndcg:.2f}"]
        cells += [f"{score_query(q, hybrid(m, chosen[m])(q), by_id).ndcg:.2f}" for m in models]
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Latency (this machine)",
        "",
        f"- Full-text query: median {statistics.median(text_ms):.1f} ms.",
        f"- Vector query (exact scan of {len(items)} rows per model): median"
        f" {statistics.median(vector_ms):.1f} ms.",
    ]
    for model, times in embed_ms.items():
        lines.append(
            f"- Query embedding, {model} (Ollama on the local GPU): median"
            f" {statistics.median(times):.0f} ms."
        )
    return lines, chosen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--models", default="nomic-embed-text,bge-m3")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "search.md")
    args = parser.parse_args()
    lines, chosen = asyncio.run(run(args.models.split(",")))
    report = "\n".join(lines) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    settings = json.dumps({m: vars(h) for m, h in chosen.items()}, indent=2)
    args.out.with_suffix(".json").write_text(settings + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
