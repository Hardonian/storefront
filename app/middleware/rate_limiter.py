"""Sliding-window IP rate limiter with response headers."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import ClassVar

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings


def get_client_ip(request: Request) -> str:
    """Extract client IP address, respecting trusted reverse proxy headers."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


class RateLimiter:
    """In-memory sliding window rate limiter."""

    def __init__(self, default_limit_per_min: int = 20):
        self.default_limit_per_min = default_limit_per_min
        self.history: dict[str, deque[float]] = defaultdict(deque)

    def is_allowed(self, key: str, limit: int | None = None, window_seconds: int = 60) -> tuple[bool, int, int]:
        """Check if an action is allowed. Returns (allowed, remaining, reset_seconds)."""
        now = time.time()
        window_limit = limit or self.default_limit_per_min
        dq = self.history[key]

        # Purge timestamps outside current window
        while dq and dq[0] < now - window_seconds:
            dq.popleft()

        remaining = max(0, window_limit - len(dq))
        reset_seconds = int(dq[0] + window_seconds - now) if dq else window_seconds

        if len(dq) >= window_limit:
            return False, 0, max(1, reset_seconds)

        dq.append(now)
        return True, remaining - 1, max(1, reset_seconds)

    def check_or_raise(self, key: str, limit: int | None = None, window_seconds: int = 60) -> None:
        """Raise HTTP 429 Too Many Requests if rate limit is exceeded."""
        allowed, _, reset_seconds = self.is_allowed(key, limit, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Please wait {reset_seconds} seconds before retrying.",
                headers={"Retry-After": str(reset_seconds)},
            )


# Global rate limiter instance
global_rate_limiter = RateLimiter(default_limit_per_min=settings.rate_limit_per_min)


def check_rate_limit(request: Request, limit: int | None = None) -> None:
    """Convenience dependency / helper for endpoint rate limiting."""
    ip = get_client_ip(request)
    global_rate_limiter.check_or_raise(ip, limit=limit or settings.rate_limit_per_min)
