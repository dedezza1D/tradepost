from fastapi import Request

from tradepost.config import get_settings


def client_ip(request: Request) -> str:
    """Client address. A forwarded header is trusted only when configured: in production the
    proxy that sets it is the only way in (see ``ProxyGuardMiddleware``)."""
    header = get_settings().client_ip_header
    if header:
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"
