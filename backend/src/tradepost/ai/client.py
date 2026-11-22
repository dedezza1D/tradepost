"""The single way the application talks to a language or embedding model.

Every call goes through ``AI``: it checks the kill switch and the spending ceiling, answers from
the cache when the same input was seen, and records tokens, latency and cost in ``ai_calls``.
Features call it and must keep working (without the AI part) when it says the model is
unavailable.

Providers: ``none`` (the default: AI off), ``ollama`` (an open model on a local Ollama server)
and ``fake`` (deterministic answers for tests).
"""

import base64
import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradepost.ai.models import AICache, AICall
from tradepost.config import Settings
from tradepost.db import Database
from tradepost.errors import AppError
from tradepost.logs import error_summary, get_logger

log = get_logger(__name__)

CACHE_TTL = timedelta(days=30)
FLAG = "ai"


class AIUnavailable(AppError):
    status_code = 503
    code = "ai_unavailable"


@dataclass(frozen=True)
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class EmbedResult:
    vectors: list[list[float]]
    input_tokens: int


class Provider(Protocol):
    name: str

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None,
        max_tokens: int,
        repeat_penalty: float | None = None,
    ) -> ChatResult: ...

    async def embed(self, model: str, texts: list[str]) -> EmbedResult: ...


class OllamaProvider:
    """An Ollama server (local in development and evaluations)."""

    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 300) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None,
        max_tokens: int,
        repeat_penalty: float | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0},
        }
        if repeat_penalty is not None:
            body["options"]["repeat_penalty"] = repeat_penalty
        if schema is not None:
            body["format"] = schema
        response = await self._http.post("/api/chat", json=body)
        response.raise_for_status()
        data = response.json()
        return ChatResult(
            text=data["message"]["content"],
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
        )

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        response = await self._http.post("/api/embed", json={"model": model, "input": texts})
        response.raise_for_status()
        data = response.json()
        return EmbedResult(
            vectors=data["embeddings"], input_tokens=int(data.get("prompt_eval_count") or 0)
        )


class FakeProvider:
    """Deterministic stand-in for tests: chat answers come from ``responders`` by feature (the
    first word of the system prompt); embeddings are a hash of the words, so equal words give
    close vectors."""

    name = "fake"

    def __init__(self, dimensions: int = 16) -> None:
        self.dimensions = dimensions
        self.responders: dict[str, Callable[[list[dict[str, Any]]], Any]] = {}
        self.calls = 0

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        schema: dict[str, Any] | None,
        max_tokens: int,
        repeat_penalty: float | None = None,
    ) -> ChatResult:
        self.calls += 1
        feature = str(messages[0]["content"]).split()[0] if messages else ""
        responder = self.responders.get(feature)
        payload = responder(messages) if responder else {}
        text_ = json.dumps(payload)
        words = sum(len(str(m["content"]).split()) for m in messages)
        return ChatResult(text=text_, input_tokens=words, output_tokens=len(text_.split()))

    async def embed(self, model: str, texts: list[str]) -> EmbedResult:
        self.calls += 1
        vectors = []
        for t in texts:
            v = [0.0] * self.dimensions
            for word in t.lower().split():
                h = int(hashlib.sha1(word.encode(), usedforsecurity=False).hexdigest(), 16)
                v[h % self.dimensions] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return EmbedResult(vectors=vectors, input_tokens=sum(len(t.split()) for t in texts))


def build_provider(settings: Settings) -> Provider | None:
    if settings.ai_provider == "ollama":
        return OllamaProvider(settings.ai_base_url)
    if settings.ai_provider == "fake":
        return FakeProvider()
    return None


async def usage_today(db: AsyncSession) -> dict[str, Any]:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    calls, tokens, cost = (
        await db.execute(
            select(
                func.count(AICall.id),
                func.coalesce(func.sum(AICall.input_tokens + AICall.output_tokens), 0),
                func.coalesce(func.sum(AICall.cost_usd), 0.0),
            ).where(AICall.created_at >= start)
        )
    ).one()
    return {"calls": int(calls), "tokens": int(tokens), "cost_usd": round(float(cost), 6)}


class AI:
    def __init__(self, settings: Settings, database: Database, provider: Provider | None) -> None:
        self.settings = settings
        self.database = database
        self.provider = provider

    @property
    def paid(self) -> bool:
        return self.settings.ai_price_in_per_mtok > 0 or self.settings.ai_price_out_per_mtok > 0

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.settings.ai_price_in_per_mtok
            + output_tokens * self.settings.ai_price_out_per_mtok
        ) / 1_000_000

    async def available(self, db: AsyncSession) -> bool:
        if self.provider is None:
            return False
        from tradepost.ops.service import flag

        if not await flag(db, FLAG):
            return False
        # A paid model stops at the daily ceiling (a ceiling of 0 means no paid calls at all).
        if self.paid:
            spent = (await usage_today(db))["cost_usd"]
            if spent >= self.settings.ai_daily_budget_usd:
                return False
        return True

    async def _require(self, db: AsyncSession) -> Provider:
        if not await self.available(db) or self.provider is None:
            raise AIUnavailable("The AI assistant is not available right now.")
        return self.provider

    async def _record(self, **fields: Any) -> None:
        # Own transaction: the cost is accounted for even if the caller's request fails.
        try:
            async with self.database.sessionmaker() as s, s.begin():
                s.add(AICall(**fields))
        except Exception:
            log.exception("ai_call_record_failed")

    async def complete_json(
        self,
        db: AsyncSession,
        feature: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        images: list[bytes] | None = None,
        max_tokens: int = 800,
        repeat_penalty: float | None = None,
        cache: bool = True,
    ) -> Any:
        """Ask for a JSON answer that follows ``schema``, optionally about some images (JPEG
        bytes, sent with the user message)."""
        provider = await self._require(db)
        model = self.settings.ai_chat_model
        prompt: dict[str, Any] = {"role": "user", "content": user}
        if images:
            prompt["images"] = [base64.b64encode(image).decode() for image in images]
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}, prompt]
        image_hashes = [hashlib.sha256(image).hexdigest() for image in images or []]
        key = hashlib.sha256(
            json.dumps(
                [feature, model, system, user, image_hashes, schema, repeat_penalty],
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        if cache:
            hit = await db.get(AICache, key)
            if hit is not None and hit.created_at > datetime.now(UTC) - CACHE_TTL:
                await self._record(
                    feature=feature, provider=provider.name, model=model, cache_hit=True
                )
                return hit.response
        start = time.perf_counter()
        try:
            result = await provider.chat(model, messages, schema, max_tokens, repeat_penalty)
            answer = json.loads(result.text)
        except Exception as exc:
            await self._record(
                feature=feature,
                provider=provider.name,
                model=model,
                latency_ms=(time.perf_counter() - start) * 1000,
                ok=False,
                error=error_summary(exc)[:300],
            )
            raise AIUnavailable("The AI assistant did not answer. Try again.") from exc
        await self._record(
            feature=feature,
            provider=provider.name,
            model=model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=(time.perf_counter() - start) * 1000,
            cost_usd=self.cost(result.input_tokens, result.output_tokens),
        )
        if cache:
            stmt = insert(AICache).values(key=key, feature=feature, response=answer)
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["key"], set_={"response": answer, "created_at": func.now()}
                )
            )
        return answer

    async def embed(self, db: AsyncSession, feature: str, texts: list[str]) -> list[list[float]]:
        provider = await self._require(db)
        model = self.settings.ai_embed_model
        start = time.perf_counter()
        try:
            result = await provider.embed(model, texts)
        except Exception as exc:
            await self._record(
                feature=feature,
                provider=provider.name,
                model=model,
                ok=False,
                error=error_summary(exc)[:300],
            )
            raise AIUnavailable("The embedding model did not answer.") from exc
        await self._record(
            feature=feature,
            provider=provider.name,
            model=model,
            input_tokens=result.input_tokens,
            latency_ms=(time.perf_counter() - start) * 1000,
            cost_usd=self.cost(result.input_tokens, 0),
        )
        return result.vectors


async def purge_cache(db: AsyncSession) -> int:
    result = await db.execute(
        text("DELETE FROM ai_cache WHERE created_at < now() - CAST(:ttl AS interval)"),
        {"ttl": CACHE_TTL},
    )
    calls = await db.execute(
        text("DELETE FROM ai_calls WHERE created_at < now() - interval '90 days'")
    )
    return int(result.rowcount or 0) + int(calls.rowcount or 0)  # type: ignore[attr-defined]
