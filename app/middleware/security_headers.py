"""Security headers middleware for defense-in-depth."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https: blob:; "
    "connect-src 'self' https:; "
    "frame-ancestors 'self'; "
    "report-uri /csp-report;"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce strict browser security headers on all responses."""

    def __init__(self, app, csp: str = DEFAULT_CSP):
        super().__init__(app)
        self.csp = csp

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = self.csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )
        return response
