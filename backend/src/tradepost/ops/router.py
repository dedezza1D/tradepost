from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from tradepost.ai.client import AI, usage_today
from tradepost.ai.client import FLAG as AI_FLAG
from tradepost.config import get_settings
from tradepost.deps import DbSession
from tradepost.errors import NotFound
from tradepost.identity.sessions import AdminUser
from tradepost.ops import service
from tradepost.ops.models import ErrorEvent

router = APIRouter(prefix="/api/admin", tags=["ops"])

KNOWN_FLAGS = frozenset({AI_FLAG})


class JobOut(BaseModel):
    name: str
    last_started: datetime
    last_ok_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    last_count: int
    last_duration_ms: float


class ErrorOut(BaseModel):
    fingerprint: str
    kind: str
    route: str
    message: str
    count: int
    first_seen: datetime
    last_seen: datetime
    last_request_id: str | None


class ErrorDetailOut(ErrorOut):
    traceback: str


class OpsOut(BaseModel):
    window_minutes: int
    traffic: dict[str, Any]
    payments: dict[str, Any]
    database: dict[str, Any]
    jobs: list[JobOut]
    errors: list[ErrorOut]
    alerts: list[dict[str, Any]]
    ai: dict[str, Any]


class FlagIn(BaseModel):
    enabled: bool


@router.get("/ops")
async def ops(
    request: Request,
    admin: AdminUser,
    db: DbSession,
    minutes: Annotated[int, Query(ge=5, le=24 * 60)] = 60,
) -> OpsOut:
    """Health of the running system for the operations page."""
    settings = get_settings()
    ai: AI = request.app.state.ai
    errors = (
        await db.execute(select(ErrorEvent).order_by(ErrorEvent.last_seen.desc()).limit(20))
    ).scalars()
    return OpsOut(
        window_minutes=minutes,
        traffic=await service.traffic(db, minutes),
        payments=await service.payments(db),
        database=await service.database_stats(db),
        jobs=[JobOut.model_validate(j, from_attributes=True) for j in await service.jobs(db)],
        errors=[ErrorOut.model_validate(e, from_attributes=True) for e in errors],
        alerts=await service.active_alerts(db, settings),
        ai={
            "provider": settings.ai_provider,
            "chat_model": settings.ai_chat_model if ai.provider else None,
            "enabled": await service.flag(db, AI_FLAG),
            "available": await ai.available(db),
            "daily_budget_usd": settings.ai_daily_budget_usd,
            **await usage_today(db),
        },
    )


@router.get("/errors/{fingerprint}")
async def error_detail(fingerprint: str, admin: AdminUser, db: DbSession) -> ErrorDetailOut:
    event = await db.get(ErrorEvent, fingerprint)
    if event is None:
        raise NotFound("Error not found.")
    return ErrorDetailOut.model_validate(event, from_attributes=True)


@router.put("/flags/{name}")
async def set_flag(name: str, body: FlagIn, admin: AdminUser, db: DbSession) -> dict[str, bool]:
    if name not in KNOWN_FLAGS:
        raise NotFound("Unknown switch.")
    await service.set_flag(db, name, body.enabled)
    return {"enabled": body.enabled}
