"""Storefront — Sovereign Public Catalog & Telemetry Gateway.

Run:
  uvicorn app.main:app --host 0.0.0.0 --port 8020
  or: ./run.sh / ./run.ps1
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app import flags as flag_engine  # noqa: F401
from app import store  # noqa: F401
from app.core.config import Settings, public_brand, require_operator, settings  # noqa: F401
from app.core.database import (
    get_db,  # noqa: F401
    get_sqlite_connection,
    init_all_services,
    init_analytics_database as _init_analytics_db,
    init_database as _init_db,  # noqa: F401
)
from app.core.security import (
    build_download_url,  # noqa: F401
    resolve_download_file as resolve_download,  # noqa: F401
    safe_external_url as _safe_external_url,  # noqa: F401
    validate_email_address as _validate_email,  # noqa: F401
    validate_slug,  # noqa: F401
)
from app.core.templates import jinja_env  # noqa: F401
from app.metrics import PrometheusMiddleware
from app.middleware.cache_control import CacheControlMiddleware
from app.middleware.cors_and_limits import PayloadLimitAndCORSMiddleware
from app.middleware.rate_limiter import check_rate_limit, get_client_ip as client_ip  # noqa: F401
from app.middleware.request_context import (
    RequestContextMiddleware,
    get_session_id as _session_id,  # noqa: F401
    get_traffic_class as _traffic_class,  # noqa: F401
)
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.observability import setup_observability
from app.routers import (
    api_analytics,
    api_flags,
    api_leads,
    api_products,
    api_support,
    blog,
    blueprint,
    catalog,
    commerce,
    finetuning,
    legal,
    private_ai_ops,
    seo,
    status,
    tools,
)
from app.routers.api_leads import LeadCreate, SubscribeCreate  # noqa: F401
from app.routers.status import _STATUS_CACHE, _collect_stack_status  # noqa: F401
from app.services.analytics_service import record_event as _record_event  # noqa: F401

logger = logging.getLogger("storefront")


# ── Backward-compatibility helpers for test fixtures & scripts ────────────────

def _analytics_connection(db_path: str):
    """Return managed SQLite connection with busy timeout."""
    return get_sqlite_connection(db_path)


def _init_analytics(db_path: str) -> None:
    """Initialize telemetry tables."""
    _init_analytics_db(db_path)


def _check_post_rate_limit(ip: str) -> None:
    """Rate limit check helper for legacy callers."""
    from app.middleware.rate_limiter import global_rate_limiter
    global_rate_limiter.check_or_raise(ip)


# ── Application Lifespan ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Lifecycle startup and shutdown hooks."""
    init_all_services()
    yield


# ── FastAPI App Assembly ──────────────────────────────────────────────────────

app = FastAPI(
    title="Storefront",
    version="0.1.0",
    description="Public-facing sovereign product catalog & conversion telemetry gateway",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Middleware Stack (Order of execution) ─────────────────────────────────────

# 1. Observability (Correlation IDs + Access Logging + /internal/* probes)
setup_observability(app, service_name="storefront", version="0.1.0")

# 2. Request context & session cookies
app.add_middleware(RequestContextMiddleware)

# 3. Prometheus metrics middleware
app.add_middleware(PrometheusMiddleware, service_name="storefront")

# 4. Strict CORS & payload body limits (413 on >64KB)
app.add_middleware(PayloadLimitAndCORSMiddleware)

# 5. Response Compression (GZip)
app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=6)

# 6. Cache Control
app.add_middleware(CacheControlMiddleware)

# 7. Strict Security Headers (CSP, X-Frame-Options: DENY, Referrer-Policy, Permissions-Policy)
app.add_middleware(SecurityHeadersMiddleware)


# ── Static Files Mounting ─────────────────────────────────────────────────────

static_path = Path(settings.static_dir)
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

landing_assets_path = static_path / "landing-assets"
if landing_assets_path.exists():
    app.mount("/landing-assets", StaticFiles(directory=str(landing_assets_path)), name="landing-assets")


# ── Include Routers ───────────────────────────────────────────────────────────

app.include_router(catalog.router)
app.include_router(api_products.router)
app.include_router(api_leads.router)
app.include_router(commerce.router)
app.include_router(api_analytics.router)
app.include_router(api_flags.router)
app.include_router(api_support.router)
app.include_router(status.router)
app.include_router(legal.router)
app.include_router(blog.router)
app.include_router(seo.router)
app.include_router(private_ai_ops.router)
app.include_router(tools.router)
app.include_router(blueprint.router)
app.include_router(finetuning.router)
