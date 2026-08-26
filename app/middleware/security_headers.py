"""Security headers and structured request logging middleware."""

from __future__ import annotations

import json
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("storefront")

DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https: blob:; "
    "connect-src 'self' https:; "
    "frame-ancestors 'none'; "
    "report-uri /csp-report;"
)

STRICT_CSP = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "script-src 'self'; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https: blob:; "
    "connect-src 'self' https:; "
    "frame-ancestors 'none'; "
    "report-uri /csp-report;"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce strict browser security headers and log structured access events."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        if request.url.path in ("/order/success", "/buyer"):
            csp = STRICT_CSP
        else:
            csp = DEFAULT_CSP

        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # Log structured request event
        rid = getattr(request.state, "request_id", response.headers.get("X-Request-ID", "unknown"))
        log_payload = {
            "event": "http_request",
            "request_id": rid,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 2),
        }
        logger.info(json.dumps(log_payload))

        return response
