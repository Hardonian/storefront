"""Storefront — Sovereign Public Catalog & Telemetry Gateway.

Run:
  uvicorn app.main:app --host 0.0.0.0 --port 8020
  or: ./run.sh / ./run.ps1
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app import flags as flag_engine
from app import store
from app.core.config import Settings, public_brand, require_operator, settings
from app.core.database import get_db, get_sqlite_connection, init_all_services
from app.core.security import (
    build_download_url,
    resolve_download_file as resolve_download,
    safe_external_url as _safe_external_url,
    validate_email_address as _validate_email,
    validate_slug,
)
from app.core.templates import jinja_env
from app.metrics import PrometheusMiddleware
from app.middleware.cache_control import CacheControlMiddleware
from app.middleware.rate_limiter import check_rate_limit, get_client_ip as client_ip
from app.middleware.request_context import (
    RequestContextMiddleware,
    get_session_id as _session_id,
    get_traffic_class as _traffic_class,
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
    catalog,
    commerce,
    legal,
    private_ai_ops,
    seo,
    status,
)
from app.routers.api_leads import LeadCreate, SubscribeCreate
from app.services.analytics_service import record_event as _record_event

logger = logging.getLogger("storefront")


# ── Database & Analytics backward-compatibility helpers ──────────────────────

def _analytics_connection(db_path: str):
    """Return managed SQLite connection with busy timeout."""
    return get_sqlite_connection(db_path)


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

# ── Middleware Stack (Registered in order of execution) ────────────────────────

# 1. Observability (Correlation IDs + Access Logging + /internal/* probes)
setup_observability(app, service_name="storefront", version="0.1.0")

# 2. Request context & session cookies
app.add_middleware(RequestContextMiddleware)

# 3. Prometheus metrics middleware
app.add_middleware(PrometheusMiddleware, service_name="storefront")

# 4. Response Compression (GZip)
app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=6)

# 5. Cache Control
app.add_middleware(CacheControlMiddleware)

# 6. Strict Security Headers (CSP, X-Frame-Options, HSTS, Permissions-Policy)
app.add_middleware(SecurityHeadersMiddleware)

# 7. CORS (Restricted to GET and public origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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
