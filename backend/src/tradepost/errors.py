from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tradepost.logs import get_logger

log = get_logger(__name__)


class AppError(Exception):
    """A domain error with a stable machine-readable code, safe to show to the client."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.extra = extra


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class TooManyRequests(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__("Too many requests, try again later.", retry_after=retry_after)
        self.retry_after = retry_after


def _body(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, **extra}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if isinstance(exc, TooManyRequests):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            _body(exc.code, exc.message, **exc.extra), status_code=exc.status_code, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"loc": list(e["loc"]), "message": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return JSONResponse(
            _body("validation_error", "Invalid request.", fields=fields), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            _body("http_error", str(exc.detail)), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Details go to the log and the error table, never to the client.
        log.exception("unhandled_error", path=request.url.path)
        from tradepost.ops.metrics import record_error, route_of

        request_id = structlog.contextvars.get_contextvars().get("request_id")
        await record_error(
            request.app.state.db,
            exc,
            f"{request.method} {route_of(request.url.path)}",
            request_id,
        )
        # The id is the only thing the client learns: it ties a report to the log entry. This
        # response is built outside the middleware that normally adds the header.
        extra = {"request_id": request_id} if request_id else {}
        headers = {"X-Request-ID": request_id} if request_id else {}
        return JSONResponse(
            _body("internal_error", "Something went wrong.", **extra),
            status_code=500,
            headers=headers,
        )
