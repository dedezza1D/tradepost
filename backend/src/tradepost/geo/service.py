"""City resolution, postal code lookup and distances.

Locations are city-level on purpose: a listing or profile shows its city, and "near you" is
measured between city centroids. No street address is ever stored.
"""

import re
import unicodedata
from collections import OrderedDict

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.errors import AppError, NotFound
from tradepost.geo.models import Municipality
from tradepost.logs import get_logger

log = get_logger(__name__)

VIACEP_URL = "https://viacep.com.br/ws/{cep}/json/"
_CEP = re.compile(r"^\d{8}$")
_cep_cache: OrderedDict[str, int | None] = OrderedDict()
_CEP_CACHE_SIZE = 2048


class UnknownCity(AppError):
    status_code = 422
    code = "unknown_city"


def name_key(name: str) -> str:
    """Lower-case, accent-free, single-spaced form used to match city names."""
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.lower().replace("'", " ").replace("-", " ").split())


async def resolve_city(db: AsyncSession, city: str, state: str) -> Municipality:
    found = (
        await db.execute(
            select(Municipality).where(
                Municipality.name_key == name_key(city), Municipality.state == state.upper()
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise UnknownCity(f"'{city}' is not a city in {state.upper()}.", city=city, state=state)
    return found


async def get_municipality(db: AsyncSession, ibge_code: int) -> Municipality:
    found = await db.get(Municipality, ibge_code)
    if found is None:
        raise NotFound("City not found.")
    return found


async def search_cities(
    db: AsyncSession, query: str, state: str | None, limit: int = 10
) -> list[Municipality]:
    key = name_key(query)
    if len(key) < 2:
        return []
    stmt = select(Municipality).where(Municipality.name_key.startswith(key, autoescape=True))
    if state:
        stmt = stmt.where(Municipality.state == state.upper())
    # Exact matches first, then shorter names (bigger cities tend to have short names).
    stmt = stmt.order_by(
        (Municipality.name_key != key), func.length(Municipality.name_key), Municipality.name
    ).limit(limit)
    return list((await db.execute(stmt)).scalars())


async def lookup_cep(db: AsyncSession, http: httpx.AsyncClient, cep: str) -> Municipality:
    digits = re.sub(r"\D", "", cep)
    if not _CEP.match(digits):
        raise AppError("A CEP has 8 digits.", code="invalid_cep")
    if digits in _cep_cache:
        code = _cep_cache[digits]
        _cep_cache.move_to_end(digits)
    else:
        try:
            response = await http.get(VIACEP_URL.format(cep=digits), timeout=5)
            data = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            log.warning("cep_lookup_failed")
            raise AppError("Postal code lookup is unavailable.", code="cep_unavailable") from None
        code = int(data["ibge"]) if data.get("ibge") and not data.get("erro") else None
        _cep_cache[digits] = code
        if len(_cep_cache) > _CEP_CACHE_SIZE:
            _cep_cache.popitem(last=False)
    if code is None:
        raise NotFound("CEP not found.", code="cep_not_found")
    return await get_municipality(db, code)
