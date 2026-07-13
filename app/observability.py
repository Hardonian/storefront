"""Hardonia cross-repo observability: request IDs, structured access logs, and
lightweight in-process metrics.

Dependency-free: uses only the standard library + Starlette (already present in
every Hardonia FastAPI service). Designed to be additive — it does not touch
existing /health, /metrics, or route handlers.

Wire it up at the TOP of app/main.py middleware stack:

    from app.observability import setup_observability
    setup_observability(app, service_name="hardonia-checkout-api", version="1.0.0")

Behaviour:
  * Ensures every request carries an X-Request-ID (reuses inbound header if
    present, otherwise mints one). Echoed back on the response.
  * Writes one structured JSON access line per request with
    request_id, method, path, status, duration_ms, client_ip, bytes.
  * Exposes a private /internal/observability/health endpoint that reports
    uptime, version, request counts, and error counts — safe to keep private.
  * Exposes GET /internal/observability/metrics with the same counters in JSON
    (Prometheus text is already handled by the existing PrometheusMiddleware;
    this is a human/automation-friendly JSON view).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

REQUEST_ID_HEADER = "X-Request-ID"
_LOG = logging.getLogger("hardonia.observability")


@dataclass
class _Counters:
    total: int = 0
    errors: int = 0
    by_status: Dict[int, int] = field(default_factory=dict)
    by_path: Dict[str, int] = field(default_factory=dict)
    latency_ms_sum: float = 0.0


class _State:
    def __init__(self, service_name: str, version: str) -> None:
        self.service_name = service_name
        self.version = version
        self.started_at = time.time()
        self.counters = _Counters()


_STATE: _State | None = None


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Respect a request_id already set by an earlier middleware (e.g. storefront's
        # request_context) so we never overwrite an existing correlation id.
        existing = getattr(request.state, "request_id", None)
        if existing:
            rid = existing
        else:
            rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = rid
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        assert _STATE is not None
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000.0
            _STATE.counters.total += 1
            _STATE.counters.errors += 1
            _LOG.error(
                "request_failed",
                extra={
                    "request_id": getattr(request.state, "request_id", "unknown"),
                    "method": request.method,
                    "path": request.url.path,
                    "client_ip": _client_ip(request),
                    "duration_ms": round(duration_ms, 2),
                    "status": 500,
                },
            )
            raise
        duration_ms = (time.perf_counter() - start) * 1000.0
        status = response.status_code
        c = _STATE.counters
        c.total += 1
        c.by_status[status] = c.by_status.get(status, 0) + 1
        c.by_path[request.url.path] = c.by_path.get(request.url.path, 0) + 1
        c.latency_ms_sum += duration_ms
        if status >= 500:
            c.errors += 1
        _LOG.info(
            "request",
            extra={
                "request_id": getattr(request.state, "request_id", "unknown"),
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": round(duration_ms, 2),
                "client_ip": _client_ip(request),
                "bytes": response.headers.get("content-length", "0"),
            },
        )
        return response


def setup_observability(app, service_name: str, version: str = "0.0.0") -> None:
    """Register observability middleware + internal endpoints on a FastAPI app.

    Idempotent: safe to call once at startup. The internal endpoints should be
    kept private (do not expose /internal/* through public ingress).
    """
    global _STATE
    if _STATE is None:
        _STATE = _State(service_name, version)
    # Request ID first so downstream middleware/logging can read it.
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(AccessLogMiddleware)

    @app.get("/internal/observability/health", include_in_schema=False)
    async def _obs_health() -> Response:
        assert _STATE is not None
        return JSONResponse(
            {
                "service": _STATE.service_name,
                "version": _STATE.version,
                "status": "ok",
                "uptime_seconds": round(time.time() - _STATE.started_at, 1),
                "total_requests": _STATE.counters.total,
                "error_requests": _STATE.counters.errors,
            }
        )

    @app.get("/internal/observability/metrics", include_in_schema=False)
    async def _obs_metrics() -> Response:
        assert _STATE is not None
        c = _STATE.counters
        avg = (c.latency_ms_sum / c.total) if c.total else 0.0
        return JSONResponse(
            {
                "service": _STATE.service_name,
                "version": _STATE.version,
                "total_requests": c.total,
                "error_requests": c.errors,
                "error_rate": round(c.errors / c.total, 4) if c.total else 0.0,
                "avg_latency_ms": round(avg, 2),
                "by_status": c.by_status,
                "top_paths": dict(sorted(c.by_path.items(), key=lambda kv: kv[1], reverse=True)[:10]),
            }
        )
