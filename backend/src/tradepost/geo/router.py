from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from tradepost.deps import DbSession
from tradepost.geo import service
from tradepost.geo.models import Municipality
from tradepost.security.ratelimit import rate_limit

router = APIRouter(prefix="/api/geo", tags=["geo"])


class CityOut(BaseModel):
    ibge_code: int
    name: str
    state: str


def city_out(m: Municipality) -> CityOut:
    return CityOut(ibge_code=m.ibge_code, name=m.name, state=m.state)


def get_http(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http
    return client


@router.get("/cities")
async def cities(
    db: DbSession,
    q: Annotated[str, Query(min_length=1, max_length=60)],
    state: Annotated[str | None, Query(max_length=2)] = None,
) -> list[CityOut]:
    return [city_out(m) for m in await service.search_cities(db, q, state)]


@router.get(
    "/cep/{cep}",
    dependencies=[Depends(rate_limit("cep", limit=30, window_seconds=600))],
)
async def cep(
    cep: str, db: DbSession, http: Annotated[httpx.AsyncClient, Depends(get_http)]
) -> CityOut:
    return city_out(await service.lookup_cep(db, http, cep))
