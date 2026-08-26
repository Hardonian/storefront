"""Request context, session cookies, and traffic classification."""

from __future__ import annotations

import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

COOKIE_NAME = "aas_sid"
REQUEST_ID_HEADER = "X-Request-ID"

BOT_KEYWORDS = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "curl/",
    "python-requests",
    "httpx",
    "headless",
    "healthcheck",
    "monitor",
    "probe",
    "uptime",
    "semrush",
    "ahrefs",
)


def get_session_id(request: Request | None) -> str:
    """Extract or return session ID from cookies or state."""
    if not request:
        return f"s_anon_{uuid.uuid4().hex[:8]}"
    if hasattr(request.state, "session_id") and request.state.session_id:
        return request.state.session_id
    cookie_val = request.cookies.get(COOKIE_NAME)
    if cookie_val:
        return cookie_val
    return getattr(request.state, "request_id", uuid.uuid4().hex)


def get_traffic_class(request: Request | None) -> str:
    """Determine coarse traffic class from request characteristics."""
    if not request:
        return "unknown"
    ua = (request.headers.get("user-agent") or "").lower()
    if any(k in ua for k in BOT_KEYWORDS):
        return "likely_bot"
    if request.headers.get("x-synthetic-test") == "true":
        return "synthetic"
    return "unknown"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Ensure correlation IDs and session tracking cookies are attached."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Correlation ID
        incoming_rid = request.headers.get(REQUEST_ID_HEADER)
        rid = incoming_rid or uuid.uuid4().hex
        request.state.request_id = rid

        # Session ID
        session_id = request.cookies.get(COOKIE_NAME)
        mint_cookie = False
        if not session_id:
            session_id = f"s{int(time.time())}_{uuid.uuid4().hex[:8]}"
            mint_cookie = True
        request.state.session_id = session_id

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid

        # Mint session cookie for browser requests
        if mint_cookie and not request.url.path.startswith("/api/"):
            response.set_cookie(
                key=COOKIE_NAME,
                value=session_id,
                max_age=2592000,  # 30 days
                path="/",
                samesite="lax",
                httponly=False,
            )

        return response
