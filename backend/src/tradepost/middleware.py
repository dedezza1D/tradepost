import hmac
import re
import time
import uuid

import structlog
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


# Room for the multipart framing around an uploaded file (boundaries, part headers, filename).
_MULTIPART_OVERHEAD = 64 * 1024
# JSON bodies: the largest field is a 5,000-character description; this leaves ample margin.
_DEFAULT_BODY_LIMIT = 128 * 1024
_WEBHOOK_BODY_LIMIT = 256 * 1024
_TOO_LARGE = "Request body too large."


class BodySizeLimitMiddleware:
    """Refuses request bodies over a per-route ceiling before anything parses them.

    A declared ``Content-Length`` over the ceiling is answered with 413 at once. A body sent
    without one (chunked) is counted as it arrives and cut off at the ceiling, so neither the
    multipart parser nor ``request.body()`` ever buffers more than the route allows.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        from tradepost.assistant import MAX_PHOTOS

        self.app = app
        photo = settings.max_photo_bytes + _MULTIPART_OVERHEAD
        self._limits: tuple[tuple[re.Pattern[str], int], ...] = (
            (re.compile(r"^/api/listings/[^/]+/photos$"), photo),
            (re.compile(r"^/api/me/avatar$"), photo),
            (re.compile(r"^/api/assist/draft$"), MAX_PHOTOS * photo),
            (re.compile(r"^/api/webhooks/"), _WEBHOOK_BODY_LIMIT),
        )

    def limit_for(self, path: str) -> int:
        for pattern, limit in self._limits:
            if pattern.match(path):
                return limit
        return _DEFAULT_BODY_LIMIT

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope["path"])
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                size = -1
            if size < 0:
                await _error(400, "bad_request", "Invalid Content-Length.")(scope, receive, send)
                return
            if size > limit:
                await _error(413, "payload_too_large", _TOO_LARGE)(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # An HTTPException passes through FastAPI's body parsing unchanged.
                    raise HTTPException(status_code=413, detail=_TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)
