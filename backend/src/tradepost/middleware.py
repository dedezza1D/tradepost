import hmac
import re
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from tradepost.config import Settings
from tradepost.logs import get_logger
from tradepost.ops.metrics import collector

log = get_logger("tradepost.http")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Called server-to-server (no browser, no Origin, no proxy for the health check).
_ORIGIN_EXEMPT_PREFIXES = ("/api/webhooks/",)
_PROXY_EXEMPT_PATHS = frozenset({"/healthz"})


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request id, access log and security headers for every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        collector.observe(request.method, request.url.path, response.status_code, elapsed_ms)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        # Path only: query strings can carry search terms typed by users.
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=elapsed_ms,
        )
        return response


class ProxyGuardMiddleware(BaseHTTPMiddleware):
    """In production the API is reachable only through the web app's proxy, which adds a shared
    secret. This keeps the origin from being called directly and makes the client-IP header
    trustworthy."""

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        secret = settings.proxy_secret
        self._secret = secret.get_secret_value().encode() if secret else None

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if self._secret and request.url.path not in _PROXY_EXEMPT_PATHS:
            sent = request.headers.get("x-proxy-secret", "").encode()
            if not hmac.compare_digest(sent, self._secret):
                return _error(403, "forbidden", "Direct access is not allowed.")
        return await call_next(request)


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """Rejects unsafe cross-origin requests. Complements SameSite cookies and the CSRF token."""

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._allowed = settings.public_origin.rstrip("/")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in _UNSAFE_METHODS and not request.url.path.startswith(
            _ORIGIN_EXEMPT_PREFIXES
        ):
            origin = request.headers.get("origin")
            if origin is not None and origin.rstrip("/") != self._allowed:
                return _error(403, "bad_origin", "Cross-origin request rejected.")
        return await call_next(request)
