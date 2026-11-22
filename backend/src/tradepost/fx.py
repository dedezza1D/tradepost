"""Reference exchange rate, for showing prices in other currencies as an estimate.

Every price, charge and payout is in reais; nothing here changes an amount. The English interface
shows a converted figure next to the price in reais, marked as approximate, so a visitor gets a
sense of the value. The rate is the Banco Central's PTAX selling rate for the US dollar, fetched by
a job a few times a day (the source updates once per business day). When it cannot be fetched,
the API answers without a rate and the interface shows reais only.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import Date, DateTime, Float, String, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tradepost.db import Base
from tradepost.deps import DbSession
from tradepost.logs import error_summary, get_logger

log = get_logger(__name__)

PTAX_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
)
REFRESH_EVERY = timedelta(hours=6)
# Weekends and holidays have no quote: look back far enough to always find one.
LOOKBACK_DAYS = 10


class FxRate(Base):
    __tablename__ = "fx_rates"

    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    quoted_on: Mapped[date] = mapped_column(Date, primary_key=True)
    # Reais for one unit of the currency.
    brl_per_unit: Mapped[float] = mapped_column(Float, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


async def fetch_usd_quotes(
    today: date, transport: httpx.AsyncBaseTransport | None = None
) -> list[tuple[date, float]]:
    """PTAX selling rates of the last days, oldest first."""
    start = today - timedelta(days=LOOKBACK_DAYS)
    params = {
        "@dataInicial": f"'{start:%m-%d-%Y}'",
        "@dataFinalCotacao": f"'{today:%m-%d-%Y}'",
        "$format": "json",
        "$select": "cotacaoVenda,dataHoraCotacao",
    }
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        response = await client.get(PTAX_URL, params=params)
        response.raise_for_status()
        rows: list[dict[str, Any]] = response.json().get("value", [])
    quotes = {
        date.fromisoformat(str(row["dataHoraCotacao"])[:10]): float(row["cotacaoVenda"])
        for row in rows
        if row.get("cotacaoVenda")
    }
    return sorted(quotes.items())


async def latest(db: AsyncSession, currency: str = "USD") -> FxRate | None:
    return await db.scalar(
        select(FxRate).where(FxRate.currency == currency).order_by(FxRate.quoted_on.desc()).limit(1)
    )


async def refresh(db: AsyncSession, transport: httpx.AsyncBaseTransport | None = None) -> int:
    """Job: store the newest dollar quotes, at most every few hours. A failure leaves the last
    known rate in place (it is only an estimate) and is logged."""
    current = await latest(db)
    now = datetime.now(UTC)
    if current is not None and current.fetched_at > now - REFRESH_EVERY:
        return 0
    try:
        quotes = await fetch_usd_quotes(now.date(), transport)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("fx_fetch_failed", error=error_summary(exc)[:200])
        return 0
    for quoted_on, rate in quotes:
        stmt = insert(FxRate).values(currency="USD", quoted_on=quoted_on, brl_per_unit=rate)
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["currency", "quoted_on"],
                set_={"brl_per_unit": rate, "fetched_at": func.now()},
            )
        )
    if not quotes and current is not None:
        # Nothing new (a long holiday): note the attempt so the next one waits its turn.
        current.fetched_at = now
    return len(quotes)


class Rate(BaseModel):
    brl_per_unit: float
    quoted_on: date


class Rates(BaseModel):
    usd: Rate | None


router = APIRouter(prefix="/api", tags=["fx"])


@router.get("/fx")
async def rates(db: DbSession, response: Response) -> Rates:
    response.headers["Cache-Control"] = "public, max-age=3600"
    usd = await latest(db)
    return Rates(usd=Rate(brl_per_unit=usd.brl_per_unit, quoted_on=usd.quoted_on) if usd else None)
