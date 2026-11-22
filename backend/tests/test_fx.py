"""Reference exchange rate: fetched from the Banco Central, served for approximate prices."""

from typing import Any

import httpx

from tests.test_checkout import sql
from tradepost.fx import refresh

PTAX = {
    "value": [
        {"cotacaoVenda": 5.61430, "dataHoraCotacao": "2026-11-19 13:08:21.353"},
        {"cotacaoVenda": 5.65180, "dataHoraCotacao": "2026-11-20 13:03:27.375"},
    ]
}


def transport(calls: list[httpx.Request], status: int = 200) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json=PTAX if status == 200 else {})

    return httpx.MockTransport(handle)


async def run_refresh(app: Any, mock: httpx.MockTransport) -> int:
    async with app.state.db.sessionmaker() as db, db.begin():
        return await refresh(db, mock)


async def test_the_latest_rate_is_stored_and_served(app: Any, client: Any) -> None:
    assert (await client.get("/api/fx")).json() == {"usd": None}  # nothing fetched yet

    calls: list[httpx.Request] = []
    assert await run_refresh(app, transport(calls)) == 2
    assert "CotacaoDolarPeriodo" in str(calls[0].url)
    body = (await client.get("/api/fx")).json()
    assert body == {"usd": {"brl_per_unit": 5.6518, "quoted_on": "2026-11-20"}}

    # Fetched recently: the next ticks do not call the source again.
    assert await run_refresh(app, transport(calls)) == 0
    assert len(calls) == 1


async def test_a_failing_source_keeps_the_last_rate(app: Any, client: Any) -> None:
    calls: list[httpx.Request] = []
    await run_refresh(app, transport(calls))
    await sql(app, "UPDATE fx_rates SET fetched_at = now() - interval '1 day'")
    assert await run_refresh(app, transport(calls, status=503)) == 0
    assert (await client.get("/api/fx")).json()["usd"]["brl_per_unit"] == 5.6518
