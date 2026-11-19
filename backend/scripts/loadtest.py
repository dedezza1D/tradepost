"""Load test for the public browsing paths (home feed, search, listing page, questions).

Usage:
    uv run python scripts/loadtest.py http://localhost:8001 --levels 1,5,10,20 --seconds 20

Each level runs that many concurrent visitors for the given time. Each visitor loops: open the
feed, search, open a listing from the results, read its questions. Rate-limited answers (429)
are counted apart from errors: the API limits each client IP on purpose, so a single machine
measures the limiter, not capacity, unless the server runs with a high TP_RATE_LIMIT_SCALE.
"""

import argparse
import asyncio
import random
import statistics
import time
from dataclasses import dataclass, field

import httpx

QUERIES = ["bicicleta", "camera", "celular", "livro", "mesa", "tenis", "violao", "monitor"]


@dataclass
class Stats:
    latencies: list[float] = field(default_factory=list)
    errors: int = 0
    limited: int = 0

    def add(self, ms: float, status: int) -> None:
        if status == 429:
            self.limited += 1
        elif status >= 500 or status == 0:
            self.errors += 1
        else:
            self.latencies.append(ms)


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def visitor(client: httpx.AsyncClient, stats: Stats, until: float) -> None:
    async def get(path: str, **params: object) -> httpx.Response | None:
        start = time.perf_counter()
        try:
            response = await client.get(path, params=params or None)
        except httpx.HTTPError:
            stats.add((time.perf_counter() - start) * 1000, 0)
            return None
        stats.add((time.perf_counter() - start) * 1000, response.status_code)
        return response

    while time.monotonic() < until:
        await get("/api/feed", tab="trending", limit=24)
        query = random.choice(QUERIES)  # noqa: S311 - test traffic, not security
        found = await get("/api/listings", q=query, limit=24)
        items = (
            found.json().get("items", []) if found is not None and found.status_code == 200 else []
        )
        if items:
            listing = random.choice(items)["id"]  # noqa: S311
            await get(f"/api/listings/{listing}")
            await get(f"/api/listings/{listing}/questions")


async def level(base: str, concurrency: int, seconds: int) -> None:
    stats = Stats()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits) as client:
        await client.get("/api/categories")  # wake the API before timing
        start = time.monotonic()
        until = start + seconds
        await asyncio.gather(*(visitor(client, stats, until) for _ in range(concurrency)))
        elapsed = time.monotonic() - start
    done = len(stats.latencies)
    mean = statistics.fmean(stats.latencies) if done else 0.0
    print(
        f"{concurrency:>4} visitors | {done / elapsed:7.1f} req/s ok | "
        f"p50 {pct(stats.latencies, 0.5):6.0f} ms | p95 {pct(stats.latencies, 0.95):6.0f} ms | "
        f"p99 {pct(stats.latencies, 0.99):6.0f} ms | errors {stats.errors} | "
        f"rate-limited {stats.limited} | mean {mean:.0f} ms"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("base_url")
    parser.add_argument("--levels", default="1,5,10,20")
    parser.add_argument("--seconds", type=int, default=20)
    args = parser.parse_args()
    print(f"target {args.base_url}, {args.seconds} s per level")
    for n in (int(x) for x in args.levels.split(",")):
        await level(args.base_url, n, args.seconds)


if __name__ == "__main__":
    asyncio.run(main())
