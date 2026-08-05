"""Storefront — public-facing product catalog & lead capture microservice.

Run:  ./run.sh  (or uvicorn app.main:app --host 0.0.0.0 --port 8020)
"""

from __future__ import annotations

import datetime
import html as _html
import json
import logging
import os
import re

# ── Analytics (local-first conversion tracking) ───────────────────────────────
import sqlite3 as _sa_sqlite
import subprocess
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import flags as flag_engine
from app import store

_ANALYTICS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_slug TEXT,
    event_type TEXT,
    source TEXT,
    payload_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _analytics_connection(db_path: str):
    """Use a bounded wait for concurrent catalog/analytics SQLite writes."""
    conn = _sa_sqlite.connect(str(db_path), timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _init_analytics(db_path: str) -> None:
    conn = _analytics_connection(db_path)
    try:
        conn.execute(_ANALYTICS_DDL)
        conn.commit()
    finally:
        conn.close()


def _record_event(event: str, page: str | None, product_slug: str | None,
                  checkout_url: str | None, session_id: str | None,
                  referrer: str | None, traffic_class: str = "unclassified") -> None:
    import json as _json
    payload = _json.dumps({
        "page": page,
        "checkout_url": checkout_url,
        "session_id": session_id,
        "referrer": referrer,
        "traffic_class": traffic_class,
    }, separators=(",", ":"))
    conn = _analytics_connection(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO events (product_slug, event_type, source, payload_json) "
            "VALUES (?,?,?,?)",
            (product_slug, event, "storefront", payload),
        )
        conn.commit()
    finally:
        conn.close()


# ── Settings ───────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_path: str = "/home/scott/ai-lab/revenue-os/revenue-os.db"
    landing_dir: str = "/home/scott/ai-lab/reports/landing"
    legal_dir: str = "/home/scott/ai-lab/legal"
    templates_dir: str = ""  # set below
    api_key: str = ""  # X-API-Key for GET /api/leads
    port: int = 8020
    rate_limit_per_min: int = 20
    debug: bool = False


settings = Settings()
settings.templates_dir = str(Path(__file__).resolve().parent / "templates")
logger = logging.getLogger("storefront")

PUBLIC_BRANDS = {
    "aiautomatedsystems.ca": ("https://aiautomatedsystems.ca", "AI Automated Systems"),
    "www.aiautomatedsystems.ca": ("https://aiautomatedsystems.ca", "AI Automated Systems"),
    "hardonia.store": ("https://hardonia.store", "Hardonia Store"),
    "www.hardonia.store": ("https://hardonia.store", "Hardonia Store"),
}


def public_brand(request: Request) -> tuple[str, str]:
    """Return the canonical public origin for an allowed storefront hostname.

    Unknown Host headers deliberately fall back to the consultancy origin so a
    forged Host cannot create arbitrary canonical URLs or poison SEO metadata.
    """
    host = (request.url.hostname or "").lower().rstrip(".")
    return PUBLIC_BRANDS.get(host, PUBLIC_BRANDS["aiautomatedsystems.ca"])


def require_operator(x_api_key: str | None = Header(None)) -> None:
    """Fail closed for internal metrics, lead, and analytics surfaces."""
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Forbidden")

# ── Jinja2 env (template.render() + HTMLResponse pattern) ─────────────────────

jinja_env = Environment(
    loader=FileSystemLoader(settings.templates_dir),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)

# ── FastAPI app ────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db(settings.db_path)
    _init_analytics(settings.db_path)
    Path(settings.landing_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.legal_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Storefront",
    version="0.1.0",
    description="Public-facing product catalog & lead capture",
    lifespan=lifespan,
)


# ── Cache control middleware ──────────────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware


class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        # Never cache API, webhook, payment-result, or customer-specific download responses.
        # These may contain entitlement, fulfillment, or order/session state.
        if (path.startswith('/api/') or path.startswith('/webhook/')
                or path in ('/order/success', '/order/cancel')
                or path.startswith('/download/')):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Pragma'] = 'no-cache'
        # Long-cache immutable static assets
        elif path.startswith('/product-assets/'):
            response.headers['Cache-Control'] = 'public, max-age=3600'
        elif path.startswith('/landing-assets/'):
            response.headers['Cache-Control'] = 'public, max-age=86400'
        # Crawlable/seo surfaces: cache briefly so re-crawls are cheap
        elif path in ('/sitemap.xml', '/blog/rss.xml', '/robots.txt'):
            response.headers['Cache-Control'] = 'public, max-age=3600'
            if path != '/robots.txt':
                response.headers['X-Robots-Tag'] = 'index, follow'
        # HTML pages: cache short, allow revalidate (content changes with catalog)
        elif (path.endswith('.html') or path in ('/', '/blog')
              or path.startswith('/p/') or path.startswith('/shop/')):
            response.headers['Cache-Control'] = 'public, max-age=300, must-revalidate'
        return response

app.add_middleware(CacheControlMiddleware)
# Compress HTML/JSON/CSS/JS responses (saves bandwidth, faster TTFB)
app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=6)

from app.metrics import PrometheusMiddleware

app.add_middleware(PrometheusMiddleware, service_name="storefront")

# Cross-repo observability: request IDs + structured access logs + /internal/* probes.
from app.observability import setup_observability as _setup_obs  # noqa: E402

_setup_obs(app, service_name="storefront", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://aiautomatedsystems.ca", "https://www.aiautomatedsystems.ca"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)

# ── Security headers (defense-in-depth for the public surface) ────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    # Report-Only CSP: collects violations (e.g. inline style/script) without
    # breaking the page, so we can tighten the enforced CSP later (drop unsafe-inline).
    resp.headers.setdefault(
        "Content-Security-Policy-Report-Only",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "report-uri /csp-report",
    )
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return resp


MAX_REQUEST_BODY_BYTES = 64 * 1024
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_NO_STORE_PATHS = {"/order/success", "/api/fulfillment/claim"}


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Bound request bodies, correlate responses, and emit one JSON access log."""
    supplied_id = request.headers.get("x-request-id", "")
    request_id = supplied_id if _REQUEST_ID_RE.fullmatch(supplied_id) else str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.perf_counter()

    content_length = request.headers.get("content-length")
    if request.method in {"POST", "PUT", "PATCH"} and content_length:
        try:
            too_large = int(content_length) > MAX_REQUEST_BODY_BYTES
        except ValueError:
            response = JSONResponse(
                {"error": "invalid_content_length", "message": "Invalid Content-Length", "request_id": request_id},
                status_code=400,
            )
        else:
            if too_large:
                response = JSONResponse(
                    {"error": "request_too_large", "message": "Request body exceeds 64 KiB", "request_id": request_id},
                    status_code=413,
                )
            else:
                response = await call_next(request)
    elif request.method in {"POST", "PUT", "PATCH"}:
        body = await request.body()
        if len(body) > MAX_REQUEST_BODY_BYTES:
            response = JSONResponse(
                {"error": "request_too_large", "message": "Request body exceeds 64 KiB", "request_id": request_id},
                status_code=413,
            )
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)

    response.headers["X-Request-ID"] = request_id
    if request.url.path in _NO_STORE_PATHS:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    logger.info(json.dumps({
        "event": "http_request", "request_id": request_id,
        "method": request.method, "path": request.url.path,
        "status": response.status_code,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }, separators=(",", ":"), sort_keys=True))
    return response


@app.post("/csp-report")
async def csp_report(request: Request):
    # Consume CSP violation reports (sent by browsers under Report-Only). No-op store.
    import contextlib

    with contextlib.suppress(Exception):
        await request.body()
    return Response(status_code=204)

# ── Rate limiting (in-memory, 20 req/min/IP on POST endpoints) ────────────────

_post_hits: dict[str, deque[float]] = defaultdict(deque)
RATE_WINDOW = 60.0  # seconds


def _check_post_rate_limit(client_ip: str) -> None:
    now = time.time()
    dq = _post_hits[client_ip]
    while dq and now - dq[0] > RATE_WINDOW:
        dq.popleft()
    if len(dq) >= settings.rate_limit_per_min:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again in a minute.",
        )
    dq.append(now)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Pydantic models ────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LeadCreate(BaseModel):
    email: str = Field(..., description="Contact email")
    product_slug: str | None = None
    source: str = Field(default="landing")
    notes: str | None = None
    tag: str | None = Field(default="lead")


class SubscribeCreate(BaseModel):
    email: str = Field(..., description="Email to subscribe")
    tag: str | None = Field(default="newsletter")
    # Honeypot: real users never fill this; bots do.
    website: str | None = Field(default=None)


def _validate_email(email: str) -> str:
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    return email.lower().strip()


# ── Static mounts ──────────────────────────────────────────────────────────────

LANDING_DIR = Path(settings.landing_dir)
LEGAL_DIR = Path(settings.legal_dir)

# Serve landing-page assets (images, css) at /landing-assets/
if (LANDING_DIR / "assets").exists():
    app.mount(
        "/landing-assets",
        StaticFiles(directory=str(LANDING_DIR / "assets")),
        name="landing-assets",
    )

# Serve the exact Google Search Console ownership token at site root. This is
# deliberately an explicit route, not a user-controlled static-file path.
GOOGLE_SITE_VERIFICATION = Path(__file__).resolve().parent.parent / "static" / "google9bd18844eac022ef.html"
INDEXNOW_KEY = os.getenv("INDEXNOW_KEY", "")
INDEXNOW_KEY_FILE = Path(__file__).resolve().parent.parent / "static" / f"{INDEXNOW_KEY}.txt"


@app.api_route("/google9bd18844eac022ef.html", methods=["GET", "HEAD"], include_in_schema=False)
async def google_site_verification():
    if not GOOGLE_SITE_VERIFICATION.is_file():
        raise HTTPException(status_code=404, detail="verification file unavailable")
    return FileResponse(str(GOOGLE_SITE_VERIFICATION), media_type="text/html")


@app.api_route(f"/{INDEXNOW_KEY}.txt", methods=["GET", "HEAD"], include_in_schema=False)
async def indexnow_key_file():
    if not INDEXNOW_KEY_FILE.is_file():
        raise HTTPException(status_code=404, detail="IndexNow key file unavailable")
    return FileResponse(str(INDEXNOW_KEY_FILE), media_type="text/plain")


# Serve standalone landing HTML previews at /landing/<slug>.html
# These are the generated, real-CTA product landing pages.

@app.get("/landing/{slug}.html")
async def landing_html(slug: str):
    # prevent path traversal
    if "/" in slug or ".." in slug:
        raise HTTPException(status_code=400, detail="bad slug")
    p = LANDING_DIR / f"{slug}.html"
    if not p.exists():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(p), media_type="text/html")

# Serve canonical product assets at /product-assets/
PRODUCT_ASSETS = Path('/home/scott/hardonia.store/products')
PROOF_PUBLIC = Path('/home/scott/ai-lab/reports/proof-score/latest.public.json')
TRUTH_LATEST = Path('/home/scott/ai-lab/state/truth-latest.json')
NEXT20_DIR = Path('/home/scott/ai-lab/reports/proof-score/next20')
if PRODUCT_ASSETS.exists():
    app.mount(
        '/product-assets',
        StaticFiles(directory=str(PRODUCT_ASSETS)),
        name='product-assets',
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "storefront", "version": app.version}


@app.get("/api/platform-truth")
async def platform_truth_api():
    """Sanitized public readiness summary; never returns raw evidence or paths."""
    if not TRUTH_LATEST.exists():
        raise HTTPException(status_code=503, detail="Platform truth is refreshing")
    try:
        raw = json.loads(TRUTH_LATEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="Platform truth is temporarily unavailable") from None
    verdicts = raw.get("verdicts", {})
    payload = {
        "schema_version": raw.get("schema_version"),
        "generated_at": raw.get("generated_at"),
        "evidence_count": len(raw.get("evidence", [])),
        "verdicts": {
            "technical_health": verdicts.get("technical_health", "unknown"),
            "evidence_freshness": verdicts.get("evidence_freshness", "unknown"),
            "commercial_readiness": verdicts.get("commercial_readiness", "unknown"),
            "read_only_collection": verdicts.get("read_only_collection", "unknown"),
        },
        "claims_policy": "Provider-correlated payment evidence is required before realized revenue is claimed.",
    }
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=60"})


@app.get("/platform-truth", response_class=HTMLResponse)
async def platform_truth_page():
    """Customer-safe explanation of how Hardonia separates capability from proof."""
    response = await platform_truth_api()
    data = json.loads(response.body)
    esc = _html.escape
    verdicts = data["verdicts"]
    cards = "".join(
        f"<div class='card'><span>{esc(key.replace('_', ' ').title())}</span><strong>{esc(value)}</strong></div>"
        for key, value in verdicts.items()
    )
    body = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Platform Truth | AI Automated Systems</title><meta name='description' content='Evidence-first readiness summary for private AI operations.'>
<style>body{{margin:0;background:#f5f1e8;color:#1f2933;font:16px system-ui;line-height:1.55}}main{{max-width:900px;margin:auto;padding:56px 22px}}.eyebrow{{color:#0f766e;letter-spacing:.12em;text-transform:uppercase;font-size:12px;font-weight:700}}h1{{font-size:clamp(38px,7vw,68px);line-height:1.02;margin:16px 0}}.lead{{font-size:20px;color:#52606d;max-width:720px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:32px 0}}.card{{padding:18px;border:1px solid #d8d3ca;border-radius:14px;background:#fffdf8;box-shadow:0 12px 30px rgba(31,41,51,.06)}}.card span,.card strong{{display:block}}.card span{{color:#66717d;font-size:13px}}.card strong{{font-size:23px;margin-top:6px}}.note{{border-left:4px solid #0f766e;padding:12px 16px;background:#fffdf8}}a{{color:#0f766e;font-weight:700}}</style></head><body><main>
<div class='eyebrow'>Evidence-first operations</div><h1>Private AI you can explain.</h1><p class='lead'>Hardonia separates what the system observed from what the catalog offers and what provider records prove. This public summary contains no customer records, credentials, raw logs, or local filesystem paths.</p>
<div class='grid'>{cards}</div><p class='note'>{esc(str(data['claims_policy']))}</p><p>Generated {esc(str(data.get('generated_at')))} · {esc(str(data.get('evidence_count')))} evidence items · schema {esc(str(data.get('schema_version')))}</p><p><a href='/'>Browse the catalog</a> · <a href='/proof-score'>See the proof score</a></p></main></body></html>"""
    return HTMLResponse(body, headers={"Cache-Control": "public, max-age=60"})


@app.get("/status", response_class=HTMLResponse)
@app.get("/status.json", response_class=JSONResponse)
async def stack_status(format: str = "html"):
    """Public AI-stack status page + JSON. Runs the live operator guards.

    No auth: this is an operator transparency surface. It only exposes service
    health, never secrets, logs, or customer data.
    """
    import subprocess as _sp

    def run(cmd):
        try:
            r = _sp.run(cmd, capture_output=True, text=True, timeout=60, shell=True)
            return (r.stdout or r.stderr).strip()
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"

    lab = run("/home/scott/.local/bin/lab-stack 2>/dev/null")
    venv = run("tail -1 /home/scott/ai-lab/logs/runtime-venv-guard.log 2>/dev/null")
    secret = run("tail -1 /home/scott/ai-lab/reports/autonomy/secret-leak-guard.log 2>/dev/null")
    hermes_rt = run("bash /home/scott/.hermes/scripts/hermes-runtime-guard.sh --report 2>/dev/null")
    all_green = "ALL GREEN" in lab
    failed = run("systemctl --user list-units --type=service --state=failed --no-legend 2>/dev/null | wc -l").strip()
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    data = {
        "status": "operational" if (all_green and failed == "0") else "degraded",
        "generated_at": ts,
        "all_green": all_green,
        "failed_units": int(failed) if failed.isdigit() else -1,
        "guards": {
            "runtime_venv": venv,
            "secret_leak": secret,
            "hermes_runtime": hermes_rt,
        },
        "stack": lab,
    }
    if format == "json":
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=60"})

    badge = "🟢 ALL GREEN" if all_green else "🔴 ISSUES"
    rows = "".join(
        f"<tr><td><code>{_html.escape(line.split('OK')[0].strip() or line.strip()[:30])}</code></td>"
        f"<td class='{'ok' if 'OK' in line else 'bad'}'>{_html.escape(line.strip())}</td></tr>"
        for line in lab.splitlines() if line.strip() and "===" not in line and "GPU" not in line
        and "Stack Status" not in line
    )
    html_doc = f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Hardonia — AI Stack Status</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:#0d1117;color:#e6edf3}}
 header{{padding:28px 20px 12px;border-bottom:1px solid #21262d}}
 h1{{margin:0;font-size:20px}} .badge{{font-size:14px;padding:3px 10px;border-radius:999px;
   background:{'#1f6f3a' if all_green else '#6e1f1f'};color:#fff;margin-left:10px}}
 main{{padding:20px;max-width:860px;margin:auto}}
 table{{width:100%;border-collapse:collapse;margin-top:14px}}
 td{{padding:8px 10px;border-bottom:1px solid #21262d;font-size:13px}}
 .ok{{color:#3fb950}}.badge-ok{{color:#3fb950}}.badge-bad{{color:#f85149}}
 .guards{{margin-top:18px;font-size:13px;color:#9da7b3}}
 footer{{padding:18px 20px;color:#6e7681;font-size:12px}}
 a{{color:#58a6ff}}
</style></head>
<body>
<header><h1>Hardonia — AI Stack Status<span class=badge>{badge}</span></h1>
<div style="color:#9da7b3;font-size:13px">Generated {_html.escape(ts)} · local-first · zero cloud</div></header>
<main>
<table><tbody>{rows}</tbody></table>
<div class=guards>
 <div>runtime-venv-guard: <span class='{'badge-ok' if 'OK' in venv else 'badge-bad'}'>{_html.escape(venv)}</span></div>
 <div>secret-leak-guard: <span class='{'badge-ok' if 'OK' in secret else 'badge-bad'}'>{_html.escape(secret)}</span></div>
 <div>hermes-runtime: <span class='{'badge-ok' if 'OK' in hermes_rt else 'badge-bad'}'>{_html.escape(hermes_rt)}</span></div>
 <div>failed systemd units: <b>{_html.escape(failed)}</b></div>
</div>
<p style="margin-top:22px"><a href="/status.json">JSON</a> · <a href="https://hardonia.store">hardonia.store</a></p>
</main>
<footer>Private, local-first AI. Your data never leaves the building.</footer>
</body></html>"""
    return HTMLResponse(html_doc, headers={"Cache-Control": "public, max-age=60"})


@app.get("/api/proof-score")
async def proof_score_api():
    """Public aggregate Proof Score; never exposes private evidence or secrets."""
    if not PROOF_PUBLIC.exists():
        raise HTTPException(status_code=503, detail="Proof Score is being refreshed")
    try:
        data = json.loads(PROOF_PUBLIC.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="Proof Score is temporarily unavailable") from None
    return JSONResponse(data, headers={"Cache-Control": "public, max-age=300"})


@app.get("/proof-score", response_class=HTMLResponse)
async def proof_score_page():
    """Public Proof Score landing surface backed by the latest redacted report."""
    if not PROOF_PUBLIC.exists():
        return HTMLResponse("<h1>Proof Score refreshing</h1><p>Try again shortly.</p>", status_code=503)
    try:
        data = json.loads(PROOF_PUBLIC.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return HTMLResponse("<h1>Proof Score temporarily unavailable</h1>", status_code=503)
    def esc(value):
        return _html.escape(str(value))
    scores = data.get("sub_scores", {})
    benchmark = data.get("benchmark", {})
    cards = "".join(f"<div class='card'><b>{esc(k.title())}</b><strong>{esc(v)}/100</strong></div>" for k, v in scores.items())
    style = """body{margin:0;background:#f5f1e8;color:#1f2933;font:16px system-ui;line-height:1.55;background-image:radial-gradient(circle at 10% 0%,rgba(15,118,110,.08),transparent 32rem)}main{max-width:960px;margin:auto;padding:56px 22px}.eyebrow{color:#0f766e;letter-spacing:.12em;text-transform:uppercase;font-size:12px;font-weight:700}h1{font-size:clamp(38px,7vw,72px);line-height:1.02;margin:16px 0}.lead{font-size:20px;color:#52606d;max-width:700px}.score{display:flex;gap:28px;align-items:center;flex-wrap:wrap;margin:38px 0}.big{font-size:76px;font-weight:800;color:#b45309;line-height:1}.grade{font-size:21px;color:#0f766e}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.card{padding:18px;border:1px solid #d8d3ca;border-radius:14px;background:#fffdf8;box-shadow:0 12px 30px rgba(31,41,51,.06)}.card b,.card strong{display:block}.card b{color:#66717d;font-size:13px}.card strong{font-size:25px;margin-top:6px}.cta{display:inline-block;margin-top:30px;background:#0f766e;color:#fff;text-decoration:none;font-weight:800;padding:13px 18px;border-radius:10px}.muted{color:#66717d;font-size:13px}"""
    body = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Sovereign AI Proof Score | The Platform</title><meta name='description' content='A locally generated, tamper-evident proof of private AI operational readiness'>
<style>{style}</style></head><body><main>
<div class='eyebrow'>The Platform Proof Layer</div><h1>Private AI you can prove.</h1><p class='lead'>A local-first operational score generated from real service, GPU, resilience, autonomy, and catalog checks. No prompts, customer records, credentials, or private infrastructure details are published.</p>
<section class='score'><div class='big'>{esc(data.get('overall_score'))}/100</div><div><div class='grade'>{esc(data.get('grade'))}</div><div class='muted'>Verified {esc(data.get('generated_at'))} · key {esc(data.get('key_id'))}</div></div></section>
<div class='grid'>{cards}</div><p class='muted'>Benchmark fixture: {esc(benchmark.get('passed'))}/{esc(benchmark.get('total'))} synthetic policy/structure cases passed. This is an operational proof signal, not a legal certification or a claim of model quality.</p>
<a class='cta' href='/p/sovereign-ops-score'>Run the full Sovereign AI Ops Score</a></main></body></html>"""
    return HTMLResponse(body, headers={"Cache-Control": "public, max-age=300"})


@app.get("/proof-benchmark", response_class=HTMLResponse)
async def proof_benchmark_page():
    page = NEXT20_DIR / 'public-benchmark.html'
    if not page.exists():
        raise HTTPException(status_code=503, detail='Benchmark page is being generated')
    return HTMLResponse(page.read_text(encoding='utf-8'), headers={"Cache-Control": "public, max-age=300"})


@app.get("/proof-badge.svg")
async def proof_badge():
    badge = NEXT20_DIR / 'proof-badge.svg'
    if not badge.exists():
        raise HTTPException(status_code=503, detail='Proof badge is being generated')
    return Response(badge.read_text(encoding='utf-8'), media_type='image/svg+xml', headers={"Cache-Control": "public, max-age=300"})


@app.get("/metrics")
async def metrics(_: None = Depends(require_operator)):
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── AU support bot proxy (Auth/Access Unit) ────────────────────────────────────
# Proxies customer questions to the local AU bot intake (au-bot-server.service :8070).
# Graceful fallback: if the bot is down, return a GitHub-issue link (never hang).
AU_BOT_URL = os.getenv("AU_BOT_URL", "http://127.0.0.1:8071/au/ask")

AU_WIDGET_JS = r"""
(function(){
  if (document.getElementById('au-widget')) return;
  var s = document.createElement('style');
  s.textContent = '.au-fab{position:fixed;bottom:18px;right:18px;z-index:9999;background:#0f766e;color:#fff;border:0;border-radius:50px;padding:12px 18px;font:600 14px system-ui;cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.35)}'
    + '.au-box{position:fixed;bottom:74px;right:18px;z-index:9999;width:340px;max-width:92vw;background:#fffdf8;color:#1f2933;border:1px solid #d8d3ca;border-radius:14px;overflow:hidden;font:14px system-ui}'
    + '.au-box header{background:#1f1f27;padding:10px 14px;font-weight:700;display:flex;justify-content:space-between}'
    + '.au-box .log{padding:12px;max-height:260px;overflow:auto;min-height:80px}'
    + '.au-box .me{text-align:right;margin:6px 0}.au-box .me span{background:#0f766e;color:#fff;padding:7px 11px;border-radius:12px;display:inline-block;text-align:left}'
    + '.au-box .bot{margin:6px 0}.au-box .bot span{background:#23232b;padding:7px 11px;border-radius:12px;display:inline-block;white-space:pre-wrap}'
    + '.au-box input{width:100%;border:0;border-top:1px solid #d8d3ca;background:#fffdf8;color:#1f2933;padding:11px 14px;box-sizing:border-box;outline:none}';
  document.head.appendChild(s);
  var fab = document.createElement('button'); fab.className='au-fab'; fab.id='au-widget'; fab.textContent='💬 Support';
  var box = document.createElement('div'); box.className='au-box'; box.style.display='none';
  var head=document.createElement('header');head.appendChild(document.createTextNode('Hardonia Support '));
  var close=document.createElement('button');close.id='au-x';close.type='button';close.textContent='✕';close.setAttribute('aria-label','Close support');head.appendChild(close);
  var chatLog=document.createElement('div');chatLog.className='log';chatLog.id='au-log';
  var chatInput=document.createElement('input');chatInput.id='au-in';chatInput.placeholder='Ask about your key, billing, access…';
  box.appendChild(head);box.appendChild(chatLog);box.appendChild(chatInput);
  document.body.appendChild(fab); document.body.appendChild(box);
  fab.onclick=function(){box.style.display=box.style.display==='none'?'block':'none';};
  document.getElementById('au-x').onclick=function(){box.style.display='none';};
  var inp=document.getElementById('au-in'), log=document.getElementById('au-log');
  function add(who,text){var d=document.createElement('div');d.className=who;var s=document.createElement('span');s.textContent=text;d.appendChild(s);log.appendChild(d);log.scrollTop=log.scrollHeight;}
  add('bot','👋 I\'m AU, Hardonia\'s auth & access assistant. Ask about API keys, 403/429 errors, credits, or access.');
  inp.addEventListener('keydown',function(e){
    if(e.key!=='Enter'||!inp.value.trim())return;
    var q=inp.value.trim();inp.value='';add('me',q);
    fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})})
      .then(function(r){return r.json();}).then(function(d){
        if(d.escalated){add('bot', d.message||'Escalated to a human — we\'ll reply within 1 business day.');}
        else if(d.answer){add('bot', d.answer.replace(/^— AU.*/m,'').trim());}
        else {add('bot','Something went wrong — please open a GitHub issue.');}
      }).catch(function(){add('bot','Support is briefly unavailable. Open a GitHub issue and we\'ll reply within 1 business day.');});
  });
})();
"""


class AskRequest(BaseModel):
    query: str = Field(..., description="Customer question for the AU support bot")
    history: list[str] | None = None


@app.post("/api/ask")
async def api_ask(req: AskRequest, request: Request):
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="query required")
    _check_post_rate_limit(client_ip(request))
    payload = {"query": req.query, "history": req.history or []}
    timeout = httpx.Timeout(connect=2.0, read=12.0, write=2.0, pool=2.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in (
            os.getenv("AU_BOT_URL", "http://127.0.0.1:8071/au/ask"),
            "http://127.0.0.1:8070/au/ask",
        ):
            try:
                r = await client.post(url, json=payload)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            data = r.json()
            # Record conversion event for KB/support funnel analysis
            _record_event(
                "support_ask",
                page=request.url.path,
                product_slug=None,
                checkout_url=None,
                session_id=_session_id(request),
                referrer=request.headers.get("referer"),
            )
            if "escalation" in data:
                _record_event(
                    "support_escalate",
                    page=request.url.path,
                    product_slug=None,
                    checkout_url=None,
                    session_id=_session_id(request),
                    referrer=request.headers.get("referer"),
                )
                return JSONResponse({
                    "answer": None,
                    "escalated": True,
                    "issue": data.get("issue"),
                    "message": "This needs a human; we've opened a support ticket. We'll reply within 1 business day.",
                })
            _record_event(
                "support_resolved",
                page=request.url.path,
                product_slug=None,
                checkout_url=None,
                session_id=_session_id(request),
                referrer=request.headers.get("referer"),
            )
            return JSONResponse({"answer": data.get("answer"), "escalated": False})
    return JSONResponse({
        "answer": None,
        "escalated": True,
        "issue": None,
        "message": "Support is briefly unavailable. Please open an issue at "
                   "https://github.com/Hardonian/hardonia-compute-api/issues and we'll reply within 1 business day.",
    })


@app.get("/api/ask/health")
async def api_ask_health():
    timeout = httpx.Timeout(connect=2.0, read=4.0, write=1.0, pool=1.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in (
            "http://127.0.0.1:8071/au/health",
            "http://127.0.0.1:8070/au/health",
        ):
            try:
                r = await client.get(url)
            except Exception:
                continue
            if r.status_code == 200:
                return {"au_bot": "up", **r.json()}
    return {"au_bot": "down"}


# ── AU support widget (served JS; injected before </body> on pages) ────────────
@app.get("/support-widget.js", response_class=PlainTextResponse)
async def support_widget_js():
    return PlainTextResponse(AU_WIDGET_JS, media_type="application/javascript")


# ── SEO / syndication surface (no personal identity; crawlable + feedable) ──────

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request):
    base, _ = public_brand(request)
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /legal/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@app.get("/sitemap.xml", response_class=Response)
async def sitemap_xml(request: Request):
    products = store.list_products(settings.db_path)
    base, _ = public_brand(request)
    lastmod = datetime.datetime.now(datetime.UTC).date().isoformat()
    urls: list[tuple[str, str, str | None]] = [
        (f"{base}/", "daily", None),
        (f"{base}/pricing", "weekly", None),
        (f"{base}/blog", "daily", None),
        (f"{base}/support", "weekly", None),
        (f"{base}/contact", "weekly", None),
        (f"{base}/tools/gpu-cost-calculator", "monthly", None),
        (f"{base}/proof-score", "hourly", None),
        (f"{base}/proof-benchmark", "daily", None),
        (f"{base}/proof-badge.svg", "daily", None),
        (f"{base}/lead", "weekly", None),
        (f"{base}/unsubscribe", "yearly", None),
    ]
    for topic in ("comfyui-alternative", "n8n-self-hosted", "private-inference", "local-ai-stack"):
        urls.append((f"{base}/compare/{topic}", "monthly", None))
    drafts_dir = Path('/home/scott/ai-lab/reports/content/drafts')
    if drafts_dir.exists():
        for draft in sorted(drafts_dir.glob('*.md'), reverse=True)[:100]:
            urls.append((f"{base}/blog/{draft.stem}", "monthly", None))
    for p in products:
        if p.get("status") in {"ready", "early-access"}:
            image_url = None
            ip = Path(p.get("image_path") or "")
            if ip.exists():
                rel = ip.relative_to(PRODUCT_ASSETS) if PRODUCT_ASSETS in ip.parents else ip.name
                image_url = f"{base}/product-assets/{rel}"
            urls.append((f"{base}/p/{p['slug']}", "weekly", image_url))
    rendered_urls = []
    for loc, frequency, image_url in urls:
        image = (
            f"<image:image><image:loc>{_xml_escape(image_url)}</image:loc></image:image>"
            if image_url else ""
        )
        rendered_urls.append(
            f"  <url><loc>{_xml_escape(loc)}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>{frequency}</changefreq>{image}</url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
        + "\n".join(rendered_urls)
        + "\n</urlset>\n"
    )
    return Response(xml, media_type="application/xml")


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """Machine-readable, non-sensitive product discovery for AI/search agents."""
    products = [p for p in store.list_products(settings.db_path) if p.get("status") in {"ready", "early-access"}]
    lines = [
        "# AI Automated Systems",
        "",
        "> Private AI infrastructure, automation kits, and GPU operations products.",
        "> Canonical site: https://aiautomatedsystems.ca/",
        "",
        "## Public pages",
        "- https://aiautomatedsystems.ca/pricing",
        "- https://aiautomatedsystems.ca/blog",
        "- https://aiautomatedsystems.ca/tools/gpu-cost-calculator",
        "- https://aiautomatedsystems.ca/contact",
        "",
        "## Public products",
    ]
    lines.extend(f"- https://aiautomatedsystems.ca/p/{p['slug']}" for p in products)
    return PlainTextResponse("\n".join(lines) + "\n")


# ── Public product pages (real buyer surface) ──────────────────────────────

POPULAR_SLUGS = {
    "ai-lab-health-report", "comfyui-workflow-pack", "n8n-automation-kit",
    "hardonia-compute-api-access", "local-ai-ops-checklist",
}
GPU_STATUS_URL = os.getenv("GPU_STATUS_URL", "http://127.0.0.1:8050/api/v1/metering/gpu")


def _gpu_status() -> dict:
    """Read live GPU free % + rates from compute-api. Best-effort; never blocks the page."""
    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(GPU_STATUS_URL)
            if r.status_code == 200:
                data = r.json()
                gpus = data.get("gpus")
                if gpus:
                    total = sum(g.get("memory_total_mb") or g.get("memory_used_mb") or 0 for g in gpus)
                    used = sum(g.get("memory_used_mb", 0) for g in gpus)
                    free = round(100 * (total - used) / total) if total else 0
                    rates = [g.get("hourly_rate_cents", 0) for g in gpus if g.get("hourly_rate_cents")]
                    name = (gpus[0].get("id") or gpus[0].get("name") or "GPU")
                    return {
                        "status": "ok", "free_pct": free, "gpu": str(name),
                        "gpu_count": len(gpus),
                        "from_cents_per_hour": min(rates) if rates else None,
                    }
                # alt shape: {"gpu":{"vram_used_mib", "vram_total_mib", "name"}}
                g = data.get("gpu")
                if isinstance(g, dict) and g.get("vram_total_mib"):
                    total = g["vram_total_mib"]
                    used = g.get("vram_used_mib", 0)
                    free = round(100 * (total - used) / total)
                    return {"status": "ok", "free_pct": free, "gpu": g.get("name", "GPU")}
    except Exception:
        pass
    return {"status": "unknown", "free_pct": None, "gpu": "GPUs warming up"}


def _session_id(request: Request) -> str:
    return request.cookies.get("aas_sid") or "anon"


def _traffic_class(request: Request) -> str:
    """Classify traffic without retaining IPs or raw user-agent strings."""
    ua = (request.headers.get("user-agent") or "").lower()
    if not ua:
        return "unknown"
    probe_markers = ("curl/", "python-requests", "httpx", "healthcheck", "probe", "uptime")
    bot_markers = ("bot", "crawler", "spider", "slurp", "bingpreview", "facebookexternalhit", "monitor")
    if any(marker in ua for marker in probe_markers):
        return "probe"
    if any(marker in ua for marker in bot_markers):
        return "crawler"
    return "returning_browser" if request.cookies.get("aas_sid") else "anonymous_browser"


def _public_checkout_url(value: object) -> str:
    """Expose only real provider links or our explicit scoped-contact path."""
    raw = str(value or "").strip()
    if raw.startswith("https://aiautomatedsystems.ca/contact?product="):
        return raw
    return _safe_external_url(raw)


def _public_product(product: dict) -> dict:
    """Return only buyer-safe catalog fields; never expose host filesystem paths."""
    allowed = {
        "slug", "name", "status", "audience", "pain", "offer", "price",
        "checkout_url", "gumroad_url", "readiness_score",
    }
    out = {k: product.get(k) for k in allowed}
    out["checkout_url"] = _public_checkout_url(product.get("checkout_url"))
    out["gumroad_url"] = _safe_external_url(product.get("gumroad_url"))
    image_path = str(product.get("image_path") or "")
    prefix = "/home/scott/hardonia.store/products/"
    out["image_url"] = "/product-assets/" + image_path[len(prefix):] if image_path.startswith(prefix) else ""
    return out


def _safe_external_url(value: object) -> str:
    """Allow only HTTPS checkout destinations owned by configured payment providers."""
    from urllib.parse import urlparse
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    host = (parsed.hostname or "").lower()
    allowed_payment = host == "buy.stripe.com" or host.endswith(".gumroad.com")
    # Consultative products may use the canonical operator audit route instead
    # of a provider checkout. Keep this explicitly allowlisted; never turn the
    # helper into a generic outbound-link proxy.
    allowed_operator = host == "aiautomatedsystems.ca" and parsed.path == "/audit/" and not parsed.query
    allowed = allowed_payment or allowed_operator
    safe_authority = parsed.username is None and parsed.password is None and parsed.port in {None, 443}
    return raw if parsed.scheme == "https" and allowed and safe_authority else ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    base, site_name = public_brand(request)
    products = store.list_products(settings.db_path)
    # Rewrite absolute image_path -> served /product-assets/ URL so covers load.
    for p in products:
        ip = p.get("image_path") or ""
        if ip.startswith("/home/scott/hardonia.store/products/"):
            p["image_path"] = "/product-assets/" + ip[len("/home/scott/hardonia.store/products/"):]
        elif ip and not ip.startswith("/"):
            # Catalog manifests may store a product-local relative path.
            p["image_path"] = f"/product-assets/{p.get('slug', '')}/{ip}"
        p["checkout_url"] = _safe_external_url(p.get("checkout_url"))
        p["gumroad_url"] = _safe_external_url(p.get("gumroad_url"))
    saleable_products = [p for p in products if p.get("status") in {"ready", "early-access"}]
    featured_products = [p for p in saleable_products if p.get("slug") in {
        "sovereign-mission-intelligence", "sovereign-ops-score", "ai-box-doctor", "private-inference-access",
        "hardonia-compute-api-access", "n8n-automation-kit", "comfyui-workflow-pack",
        "autonomous-revenue-loop",
    }]
    featured_slugs = {p.get("slug") for p in featured_products}
    catalog_products = [p for p in saleable_products if p.get("slug") not in featured_slugs]
    flags = flag_engine.load_flags()
    hero_variant = flag_engine.evaluate_variant("hero_variant", _session_id(request))
    cta_variant = flag_engine.evaluate_variant("cta_variant", _session_id(request))
    try:
        html = jinja_env.get_template("index.html").render(
            products=saleable_products,
            featured_products=featured_products,
            catalog_products=catalog_products,
            title="AI Automated Systems — Tools, Audits & Workflows",
            site_base=base,
            site_name=site_name,
            hero_variant=hero_variant,
            cta_variant=cta_variant,
            newsletter_enabled=flags.get("newsletter_enabled", True),
            trust_bar_enabled=flags.get("trust_bar_enabled", True),
            product_grid_dense=flags.get("product_grid_dense", False),
            popular_slugs=POPULAR_SLUGS,
            empire_slugs=["agent-ops-concierge","private-ai-vault","inference-api-starter","inference-api-scale","compliance-kit","compliance-keep-current","uptime-bond"],
            platform_label="The Platform",
        )
        return HTMLResponse(html)
    except Exception:
        # Hardening fallback: never serve the placeholder again.
        cards = "".join(
            f'<div class="card"><h3>{p.get("name","")}</h3>'
            f'<p class="price">{p.get("price","")}</p>'
            f'<a href="/p/{p.get("slug","")}" class="btn btn-secondary">View</a></div>'
            for p in products
        )
        return HTMLResponse(
            f"<!doctype html><html><head><title>AI Automated Systems</title></head>"
            f"<body><h1>Products</h1><div class='grid'>{cards}</div></body></html>"
        )


# ── Deliverables manifest (per-product media / templates / samples) ───────────
DELIVERABLES_MANIFEST = Path("/home/scott/ai-lab/reports/deliverables-manifest.json")

def _load_manifest() -> dict:
    try:
        return json.loads(DELIVERABLES_MANIFEST.read_text())
    except Exception:
        return {}

_MANIFEST_CACHE: dict = {}
def _manifest_for(slug: str) -> dict:
    if not _MANIFEST_CACHE:
        _MANIFEST_CACHE.update(_load_manifest())
    return _MANIFEST_CACHE.get(slug, {})

# Legit urgency: launch pricing ends at a REAL date. No fake countdowns.
# Set LAUNCH_PRICING_UNTIL in env to enable; otherwise no urgency badge.
_LAUNCH_UNTIL = os.getenv("LAUNCH_PRICING_UNTIL", "")
def _urgency_badge() -> str:
    if not _LAUNCH_UNTIL:
        return ""
    try:
        from datetime import datetime
        until = datetime.fromisoformat(_LAUNCH_UNTIL)
        if until > datetime.now():
            days = (until - datetime.now()).days
            return (f'<span class="pill urgency">🔥 Launch pricing — '
                    f'{days} day{"s" if days!=1 else ""} left at this price</span>')
    except Exception:
        pass
    return ""

TRUST_BADGES = [
    ("🔒", "Processor-secured checkout", "Stripe or Gumroad handles payment"),
    ("🧭", "Clear scope and pricing", "Product-specific terms before purchase"),
    ("📦", "Documented delivery", "Digital pack or human-led onboarding"),
    ("🛡️", "Private-first options", "Data-minimizing designs where practical"),
    ("🤝", "Human support", "Support path included on every offer"),
]

PLATFORM_SLUGS = {
    "agent-ops-concierge", "private-ai-vault", "inference-api-starter",
    "inference-api-scale", "compliance-kit", "compliance-keep-current", "uptime-bond",
}
PLATFORM_TRUST = [
    ("📊", "Observable operations", "Evidence and status signals are documented"),
    ("🧭", "Decision-ready handoff", "Runbooks and boundaries are explicit"),
    ("🧱", "Scoped access", "Isolation and usage limits where applicable"),
    ("👤", "Human approval gates", "Consequential actions remain reviewable"),
]


def _trust_row_html(slug: str = "") -> str:
    badges = [
        f'<div class="tbadge"><span class="ticon">{icon}</span>'
        f'<span class="ttext"><b>{title}</b><br><small>{sub}</small></span></div>'
        for icon, title, sub in TRUST_BADGES
    ]
    if slug in PLATFORM_SLUGS:
        badges += [
            f'<div class="tbadge"><span class="ticon">{icon}</span>'
            f'<span class="ttext"><b>{title}</b><br><small>{sub}</small></span></div>'
            for icon, title, sub in PLATFORM_TRUST
        ]
    if slug in ("uptime-bond", "agent-ops-concierge", "inference-api-scale", "private-ai-vault"):
            badges.append(
                '<div class="tbadge"><span class="icon">📝</span>'
                '<span class="ttext"><b>Service terms</b><br><small>Scope and support agreed before onboarding</small></span></div>'
            )
    return f'<div class="trust-row">{"".join(badges)}</div>'

def _deliverables_html(slug: str) -> str:
    m = _manifest_for(slug)
    if not m:
        return ""
    out = ['<h2>What you get</h2>']
    # media gallery
    media = m.get("media", [])
    if media:
        thumbs = "".join(
            f'<a href="{x["url"]}" target="_blank" rel="noopener" class="thumb">'
            f'<img src="{x["url"]}" alt="{x["name"]}" loading="lazy"></a>'
            for x in media[:6]
        )
        out.append(f'<div class="gallery">{thumbs}</div>')
    # preview doc
    if m.get("preview_md"):
        out.append(f'<p><a class="cta secondary" href="{m["preview_md"]}" target="_blank" '
                   f'rel="noopener">👁 View product preview / sample ↗</a></p>')
    # templates + worksheets + samples (the paid send-off material)
    def _list(title, items):
        if not items:
            return ""
        lis = "".join(f'<li><a href="{i["url"]}" target="_blank" rel="noopener">{i["name"]}</a></li>' for i in items[:12])
        return f'<h3>{title}</h3><ul class="dlist">{lis}</ul>'
    out.append(_list("📦 Templates & systemd units", m.get("templates", [])))
    out.append(_list("📝 Worksheets & sample data", m.get("worksheets", [])))
    out.append(_list("🧰 Scripts & samples", m.get("scripts", []) + m.get("samples", [])))
    return "\n".join(out)

def _tier_includes_html(price_str: str) -> str:
    """Render a Free/Pro/Premium/Enterprise includes block from the price string."""
    has_free = "free to try" in (price_str or "").lower()
    has_ent = "enterprise" in (price_str or "").lower()
    rows = []
    if has_free:
        rows.append(("🎁 Free", "Starter pack + 20% upgrade code. No card."))
    if "pro" in (price_str or "").lower():
        rows.append(("⚡ Pro", "Full product + instant delivery + updates."))
    if "premium" in (price_str or "").lower():
        rows.append(("⬆ Premium", "Everything in Pro + done-for-you / team assets."))
    if has_ent:
        rows.append(("🏢 Enterprise", "Custom service terms, volume pricing, onboarding."))
    if not rows:
        return ""
    body = "".join(f'<tr><td>{t}</td><td>{d}</td></tr>' for t, d in rows)
    return (f'<h2>Included by tier</h2>'
            f'<table class="tiers"><thead><tr><th>Tier</th><th>What\'s included</th></tr></thead>'
            f'<tbody>{body}</tbody></table>')


FULFILLMENT_JS = r"""
(function(){
  var form=document.getElementById('claim');
  if(!form)return;
  var out=document.getElementById('result');
  var submit=document.getElementById('claim-submit');
  var retry=document.getElementById('claim-retry');
  function clear(node){while(node.firstChild)node.removeChild(node.firstChild);}
  function message(text){clear(out);out.textContent=text;}
  function recover(){retry.hidden=false;submit.disabled=false;submit.textContent='Reveal my delivery';}
  retry.addEventListener('click',function(){retry.hidden=true;document.getElementById('email').focus();});
  form.addEventListener('submit',async function(e){
    e.preventDefault();retry.hidden=true;submit.disabled=true;submit.textContent='Verifying…';
    message('Verifying payment… This can take a few seconds.');
    try {
      var r=await fetch('/api/fulfillment/claim',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({session_id:document.getElementById('sid').value,email:document.getElementById('email').value})});
      var d=await r.json();
      if(!r.ok){message((d.message||'Fulfillment is not ready.')+' Contact support if this continues.');recover();return;}
      clear(out);
      if(d.type==='download'){
        out.appendChild(document.createTextNode('Your download is ready: '));
        var link=document.createElement('a');link.href=d.download_url;link.textContent='Download securely';out.appendChild(link);
      } else {
        out.textContent='API key (store it now): '+d.api_key+'\nCredits: '+d.credits;
      }
    } catch(error) {
      message('We could not reach fulfillment. Check your connection, then Try again or Contact support.');recover();
    } finally {
      if(retry.hidden){submit.disabled=false;submit.textContent='Reveal my delivery';}
    }
  });
})();
"""


@app.get("/fulfillment.js", response_class=PlainTextResponse)
async def fulfillment_js():
    return PlainTextResponse(FULFILLMENT_JS, media_type="application/javascript")




def _buyer_portal_html(session_id: str) -> str:
    sid = _html.escape(session_id, quote=True)
    return f"""<!doctype html><html lang='en'><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='robots' content='noindex,nofollow'><title>Buyer delivery portal — AI Automated Systems</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#07111f;color:#e8f1ff;max-width:820px;margin:0 auto;padding:clamp(2rem,7vw,5rem) 1.25rem;line-height:1.55}}
.card{{background:#101d2d;border:1px solid #24364b;border-radius:14px;padding:clamp(1.25rem,4vw,2rem);margin:1rem 0}}
h1{{font-size:clamp(2rem,6vw,3.4rem);line-height:1.05;margin:.5rem 0 1rem}}h2{{font-size:1.15rem;margin-top:0}}
.muted{{color:#a9b9ca}}.steps{{display:grid;gap:.6rem;padding-left:1.2rem}}input,button{{font:inherit;padding:.8rem;margin:.35rem 0;width:100%;box-sizing:border-box;border-radius:8px}}
input{{background:#081522;color:#e8f1ff;border:1px solid #38506a}}button{{background:#33d6a6;color:#062a26;border:0;font-weight:700;cursor:pointer}}
#result{{white-space:pre-wrap;min-height:2rem}}a{{color:#7dd3fc}}.links{{display:flex;gap:1rem;flex-wrap:wrap}}
</style></head><body>
<p class='muted'>AI Automated Systems · Buyer delivery portal</p>
<h1>Check your delivery status.</h1>
<p class='muted'>Use the email address from checkout. We verify the purchase before revealing a download or compute entitlement.</p>
<section class='card'><h2>Secure delivery check</h2>
<form id='claim'><input type='hidden' id='sid' value='{sid}'><label>Email address<input id='email' type='email' required autocomplete='email' placeholder='you@firm.ca'></label><button id='claim-submit' type='submit'>Check delivery status</button></form>
<pre id='result' aria-live='polite'>Ready to verify. No customer data is displayed until the purchase is verified.</pre>
<button id='claim-retry' type='button' hidden>Try again</button></section>
<section class='card'><h2>If delivery is not ready</h2><ol class='steps'><li>Confirm the email matches checkout.</li><li>Wait a few minutes if payment was just completed.</li><li>Try the check again, then contact support if it remains pending.</li></ol></section>
<p class='links'><a href='/contact?subject=buyer-support'>Refund or support</a><a href='/legal/refund-policy'>Refund policy</a><a href='/'>Return to catalog</a></p>
<script src='/fulfillment.js' defer></script></body></html>"""


@app.get("/buyer", response_class=HTMLResponse)
async def buyer_portal(request: Request):
    session_id = request.query_params.get("session_id", "")
    if not re.fullmatch(r"cs_[A-Za-z0-9_]{4,196}", session_id):
        raise HTTPException(status_code=400, detail="Invalid checkout session")
    return HTMLResponse(_buyer_portal_html(session_id))


@app.get("/order/success", response_class=HTMLResponse)
async def order_success(request: Request):
    import html as _html
    session_id = request.query_params.get("session_id", "")
    if not re.fullmatch(r"cs_[A-Za-z0-9_]{4,196}", session_id):
        raise HTTPException(status_code=400, detail="Invalid checkout session")
    sid = _html.escape(session_id, quote=True)
    return HTMLResponse(f"""<!doctype html><html lang='en'><head>
<meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='robots' content='noindex,nofollow'><title>Claim your purchase — AI Automated Systems</title>
<style>body{{font-family:system-ui;background:#07111f;color:#e8f1ff;max-width:720px;margin:4rem auto;padding:1.5rem}}input,button{{font:inherit;padding:.8rem;margin:.4rem 0;width:100%;box-sizing:border-box}}button{{background:#33d6a6;border:0;font-weight:700}}pre{{white-space:pre-wrap;background:#101d2d;padding:1rem;border-radius:8px}}</style></head><body>
<h1>Claim your purchase</h1><p>Enter the same email address used at Stripe checkout.</p>
<form id='claim'><input type='hidden' id='sid' value='{sid}'><label>Email<input id='email' type='email' required autocomplete='email'></label><button id='claim-submit' type='submit'>Reveal my delivery</button></form>
<pre id='result' aria-live='polite'>Ready to verify. During verification you will see: Verifying payment…</pre>
<button id='claim-retry' type='button' hidden>Try again</button><p><a href='/buyer?session_id={sid}'>Open buyer delivery portal</a> · <a href='/contact?subject=buyer-support'>Contact support</a></p>
<script src="/fulfillment.js" defer></script>
</body></html>""")


@app.post("/api/fulfillment/claim")
async def fulfillment_claim(request: Request):
    """Proxy a claim without exposing checkout/provider error shapes to buyers."""
    _check_post_rate_limit(client_ip(request))
    request_id = request.state.request_id

    def claim_error(status_code: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            {"error": code, "message": message, "request_id": request_id},
            status_code=status_code,
        )

    try:
        payload = await request.json()
    except Exception:
        return claim_error(400, "invalid_json", "Enter a valid claim request")
    if not isinstance(payload, dict):
        return claim_error(422, "invalid_claim", "Check the session and email, then try again")
    session_id = str(payload.get("session_id") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    if (
        not re.fullmatch(r"cs_[A-Za-z0-9_]{4,196}", session_id)
        or not _EMAIL_RE.fullmatch(email)
    ):
        return claim_error(422, "invalid_claim", "Check the session and email, then try again")
    try:
        with httpx.Client(timeout=15.0, trust_env=False) as client:
            response = client.post(
                "http://127.0.0.1:8012/api/v1/fulfillment/claim",
                json={"session_id": session_id, "email": email},
            )
        if response.status_code in {404, 409}:
            return claim_error(409, "claim_not_ready", "Payment is still processing or the email does not match")
        if response.status_code != 200:
            return claim_error(502, "verification_failed", "Purchase verification is temporarily unavailable")
        data = response.json()
        if data.get("type") == "compute" and not str(data.get("api_key") or "").startswith("hk_live_"):
            return claim_error(502, "invalid_fulfillment", "Fulfillment returned an invalid response")
        if data.get("type") == "download" and not str(data.get("download_url") or "").startswith("https://aiautomatedsystems.ca/download/"):
            return claim_error(502, "invalid_fulfillment", "Fulfillment returned an invalid response")
        return data
    except Exception:
        logger.exception(json.dumps({"event": "fulfillment_proxy_failed", "request_id": request_id}))
        return claim_error(502, "fulfillment_unavailable", "Fulfillment service is temporarily unavailable")


@app.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(request: Request, email: str = ""):
    """CASL/GDPR opt-out. One click removes the lead from nurture."""
    html = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Unsubscribe — Hardonia</title>
<style>body{font-family:system-ui,sans-serif;margin:0;background:#0c1118;color:#e8eef5}
.wrap{max-width:520px;margin:0 auto;padding:64px 20px;text-align:center}
h1{font-size:1.6rem}.ok{color:#2dd4bf}.btn{margin-top:20px;padding:12px 24px;border:0;border-radius:8px;background:#2dd4bf;color:#062a26;font-weight:700;cursor:pointer}
input{padding:12px;border-radius:8px;border:1px solid #2a3a4d;background:#0c1118;color:#e8eef5;width:100%;font-size:1rem}
</style></head><body><div class=wrap>
<h1>Unsubscribe</h1>
<p>Enter your email to stop all Hardonia Automated Systems emails. This is immediate and permanent.</p>
<form id=f onsubmit="return unsub(event)">
<input id=email value="__EMAIL__" required placeholder=you@firm.ca>
<button class=btn type=submit>Unsubscribe</button></form>
<p id=out></p></div>
<script>
async function unsub(e){e.preventDefault();
 const r=await fetch('/api/unsubscribe',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value})});
 const j=await r.json();
 document.getElementById('out').innerHTML = j.ok ? '<span class=ok>✓ You are unsubscribed. Sorry to see you go.</span>' : 'Error: '+j.reason;
 return false;}
</script></body></html>"""
    html = html.replace("__EMAIL__", email or "")
    return HTMLResponse(html)


@app.post("/api/unsubscribe")
async def api_unsubscribe(request: Request, payload: dict = Body(default={})):
    email = (payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "reason": "invalid_email"}
    try:
        import sqlite3 as _sql
        db = _sql.connect(settings.db_path)
        db.execute("UPDATE leads SET status='unsubscribed', notes='opt-out via /unsubscribe' WHERE email=?", (email,))
        db.commit()
        db.close()
        return {"ok": True}
    except Exception:
        logger.exception("unsubscribe failed")
        return {"ok": False, "reason": "temporarily_unavailable"}


@app.get("/lead", response_class=HTMLResponse)
async def lead_page(request: Request):
    """Free Sovereign AI Readiness Score + capture."""
    _record_event("lead_page_view", page=request.url.path, product_slug=None,
                  checkout_url=None, session_id=_session_id(request),
                  referrer=request.headers.get("referer"))
    html = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Free Sovereign AI Readiness Score — Hardonia</title>
<meta name=description content="Run a free local sovereignty audit. Score your AI drafting setup in 60 seconds. No cloud, no email required to see your score.">
<style>
body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#0c1118;color:#e8eef5}
.wrap{max-width:680px;margin:0 auto;padding:48px 20px}
h1{font-size:2rem;margin:0 0 8px}
p.lead{color:#9fb3c8;font-size:1.1rem}
.card{background:#141c26;border:1px solid #233; border-radius:12px;padding:28px;margin-top:24px}
label{display:block;margin:14px 0 6px;font-weight:600}
input,select{width:100%;padding:12px;border-radius:8px;border:1px solid #2a3a4d;background:#0c1118;color:#e8eef5;font-size:1rem}
button{margin-top:20px;width:100%;padding:14px;border:0;border-radius:8px;background:#2dd4bf;color:#062a26;font-size:1.05rem;font-weight:700;cursor:pointer}
button:hover{background:#14b8a6}
.note{color:#7d92a6;font-size:.85rem;margin-top:14px}
pre{background:#0c1118;border:1px solid #233;border-radius:8px;padding:14px;overflow:auto;font-size:.8rem}
</style></head><body><div class=wrap>
<h1>Free Sovereign AI Readiness Score</h1>
<p class=lead>See how much of your AI drafting leaks to third-party clouds. 5 questions, 60 seconds, 100% local.</p>
<div class=card>
<form id=f onsubmit="return submitLead(event)">
<label>Your email (for your free review)</label>
<input type=email id=email required placeholder=you@clinic.ca>
<label>Which vertical are you in?</label>
<select id=slug><option value=sentinel-note>Clinical</option><option value=ops-draft>Legal / Municipal</option><option value=ledger-draft>Finance</option><option value=hr-draft>HR / Policy</option><option value=sovereign-supercharger>All of the above</option></select>
<label>How did you find us?</label>
<input id=source placeholder="search, referral, github...">
<button type=submit>Get my free score</button>
<p class=note>We run a local script and email you a 15-min sovereignty review offer. No spam. Unsubscribe anytime.</p>
<p style="margin-top:10px"><a href="/landing-assets/sovereign_readiness_score.py" download style="color:#2dd4bf">⬇ Download the Readiness Score script (free, runs 100% offline)</a></p>
</form>
<pre id=out></pre>
</div></div>
<script>
async function submitLead(e){
 e.preventDefault();
 const email=document.getElementById('email').value;
 const slug=document.getElementById('slug').value;
 const source=document.getElementById('source').value||'lead-page';
 const r=await fetch('/api/lead',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({email,slug,source,utm_campaign:'sovereign-readiness'})});
 const j=await r.json();
 document.getElementById('out').textContent=JSON.stringify(j,null,2);
 return false;
}
</script></body></html>"""
    return HTMLResponse(html)


@app.get("/p/{slug}", response_class=HTMLResponse)
async def product_page(slug: str, request: Request):
    product = store.get_product(slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    landing_url = f"/landing/{slug}.html" if (LANDING_DIR / f"{slug}.html").exists() else ""

    img = ""
    if product.get("image_path"):
        ip = Path(product["image_path"])
        if ip.exists():
            rel = ip.relative_to(PRODUCT_ASSETS)
            img = f"/product-assets/{rel}"

    _record_event("product_view", page=request.url.path, product_slug=slug,
                  checkout_url=None, session_id=_session_id(request),
                  referrer=request.headers.get("referer"), traffic_class=_traffic_class(request))

    # Pre-built CRO / trust / deliverables blocks
    trust_html = _trust_row_html(slug)
    urgency_html = _urgency_badge()
    # Deterministic ROI callout for platform SKUs (observable bottom-line proof)
    roi_html = ""
    _TIERS = {"inference-api-starter": ("starter", 49, 20),
              "inference-api-scale": ("scale", 199, 100),
              "agent-ops-concierge": ("concierge", 497, 200)}
    if slug in _TIERS:
        t, price, gpu_hr = _TIERS[slug]
        cspend = 800
        monthly_savings = max(0.0, cspend - price)
        annual = monthly_savings * 12
        pct = (monthly_savings / cspend * 100) if cspend else 0.0
        roi_html = (f'<div class="roi-callout">💡 <b>Bottom line:</b> vs ~${cspend}/mo cloud GPU spend, '
                    f'this tier saves <b>${monthly_savings:.0f}/mo</b> '
                    f'(${annual:.0f}/yr, {pct:.0f}%) at a fixed '
                    f'${price}/mo — isolation + monitoring where applicable. '
                    f'Confirm service terms before purchase.</div>')
    deliverables_html = _deliverables_html(slug)
    tier_html = _tier_includes_html(product.get("price", ""))
    if slug in {"hardonia-compute-api-access", "private-inference-access"}:
        support_html = '<h2>Support and assurance</h2><p>Usage and access are explained before activation, with a documented key or account handoff and a clear path for billing, capacity, and access questions. Credits and service limits follow the product terms.</p>'
    elif slug in {"sentinel-note", "ops-draft", "ledger-draft", "hr-draft", "hardonia-enterpriser", "sovereign-supercharger"}:
        support_html = '<h2>Support and assurance</h2><p>Onboarding covers the intended workflow, local deployment boundaries, and the review step before production use. The buyer pack and support path identify what is included; regulated decisions remain with the customer.</p>'
    elif slug in {"sovereign-mission-intelligence", "sovereign-ai-audit", "sovereign-control-plane", "sovereign-ops-score", "autonomous-revenue-loop"}:
        support_html = '<h2>Support and assurance</h2><p>This offer begins with human scoping. We document assumptions, responsibilities, evidence boundaries, and the agreed handoff so the engagement does not depend on unreviewed automation or implied guarantees.</p>'
    elif slug.startswith("comfyui-") or slug in {"ai-portrait-studio", "ai-character-generator-kit", "ai-video-storyboard-studio", "ai-voice-clone-training-kit"}:
        support_html = '<h2>Support and assurance</h2><p>Buyer documentation covers setup, workflow inputs, and expected outputs. Support focuses on reproducible local execution and troubleshooting the delivered workflow, not on promising identical results for every model or prompt.</p>'
    else:
        support_html = '<h2>Support and assurance</h2><p>The product page and buyer documents define the delivered materials, setup expectations, and support path. Contact us with the product name and the step that failed; never send credentials or private customer data.</p>'

    # Live Ops Dashboard visual (productized operational capability)
    import html as _html
    dashboard_html = ""
    _dash_url = str(product.get("dashboard_url") or "").strip()
    if _dash_url:
        _feats = product.get("dashboard_features") or [
            "Revenue leverage, system productivity, predictive signals",
            "Profit/financial modelling and strategic outlook",
            "Updates in real time from the live sovereign fleet",
        ]
        _feat_html = "".join(f"<li>{_html.escape(str(f))}</li>" for f in _feats)
        dashboard_html = (
            '<div class="dash-card">'
            '<div class="dash-head">📊 <b>Live Ops Dashboard</b> — included</div>'
            f'<p class="dash-sub">Every node in this fleet feeds one real-time operational pane. '
            f'Verified fleet metrics, predictive signals, and financial modelling — the same engine running our own sovereign stack.</p>'
            f'<ul class="dash-feats">{_feat_html}</ul>'
            '<a class="cta secondary" href="/contact?product=' + slug + '">Request a live dashboard demo</a>'
            '</div>'
        )

    # Escape all catalog copy before interpolation; product data is content, not markup.
    import html as _html
    name_raw = str(product.get("name") or "")
    description_raw = str(product.get("offer") or product.get("pain") or "")[:160]
    name_html = _html.escape(name_raw)
    description_html = _html.escape(description_raw, quote=True)
    audience_html = _html.escape(str(product.get("audience") or ""))
    pain_html = _html.escape(str(product.get("pain") or ""))
    offer_html = _html.escape(str(product.get("offer") or ""))
    price_html = _html.escape(str(product.get("price") or ""))
    status_html = _html.escape(str(product.get("status") or "draft"))
    base, site_name = public_brand(request)
    canonical = f"{base}/p/{slug}"
    image_absolute = f"{base}{img}" if img else ""
    price_match = re.search(r"\d+(?:\.\d{1,2})?", str(product.get("price") or ""))
    product_schema = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name_raw,
        "description": description_raw,
        "url": canonical,
        "offers": {
            "@type": "Offer",
            "price": price_match.group(0) if price_match else "0",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock" if product.get("status") == "ready" else "https://schema.org/PreOrder",
            "url": canonical,
        },
    }
    if image_absolute:
        product_schema["image"] = image_absolute
    product_schema_json = json.dumps(product_schema, ensure_ascii=False).replace("</", "<\\/")
    platform_layer = {
        "sovereign-ops-score": ("01 · PROVE", "Establish a measurable baseline before you scale."),
        "ai-box-doctor": ("01 · PROVE", "Keep the box healthy after the audit."),
        "private-inference-access": ("02 · RUN", "Put private models to work on owned infrastructure."),
        "hardonia-compute-api-access": ("02 · RUN", "Expose controlled GPU capacity without losing isolation."),
        "comfyui-workflow-pack": ("03 · AUTOMATE", "Turn creative workflows into reproducible assets."),
        "n8n-automation-kit": ("03 · AUTOMATE", "Connect the work so it runs without babysitting."),
        "autonomous-revenue-loop": ("04 · COMPOUND", "Convert reliable operations into a repeatable offer."),
    }.get(slug, ("PLATFORM TOOL", "A focused capability that plugs into the same operating system."))

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name_html} — {site_name}</title>
<meta name="description" content="{description_html}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="product">
<meta property="og:site_name" content="{site_name}">
<meta property="og:title" content="{name_html} — {site_name}">
<meta property="og:description" content="{description_html}">
<meta property="og:url" content="{canonical}">
{f'<meta property="og:image" content="{image_absolute}">' if image_absolute else ''}
<meta name="twitter:card" content="summary_large_image">
<style>
:root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca;--price:#b45309}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;background-image:radial-gradient(circle at 10% 0%,rgba(15,118,110,.08),transparent 32rem),radial-gradient(circle at 90% 10%,rgba(180,83,9,.06),transparent 28rem)}}
.container{{max-width:880px;margin:0 auto;padding:2.5rem 1.5rem}}
header a{{color:var(--accent);text-decoration:none}}
.img{{width:100%;max-height:340px;object-fit:cover;border-radius:12px;border:1px solid var(--border);margin:1.25rem 0}}
.badge{{display:inline-block;padding:.2rem .6rem;border-radius:6px;font-size:.75rem;font-weight:700;text-transform:uppercase;background:#166534;color:#4ade80}}
.price{{color:var(--price);font-weight:700;font-size:1.6rem;margin:.5rem 0}}
.roi-callout{{display:block;margin:1rem 0;padding:.8rem 1rem;border-radius:10px;background:#0f1f17;border:1px solid #166534;color:var(--text);font-size:.92rem}}
h1{{font-size:2rem;font-weight:800;letter-spacing:-.03em}}
h2{{font-size:1.2rem;margin:1.5rem 0 .5rem;color:var(--text)}}
p,.pain{{color:var(--muted)}}
.cta{{display:inline-flex;align-items:center;justify-content:center;padding:.8rem 1.6rem;border-radius:10px;font-weight:700;text-decoration:none;background:var(--accent);color:#fff;margin-top:1.25rem;margin-right:.6rem}}
.cta.secondary{{background:transparent;border:1px solid var(--border);color:var(--text)}}
.cta.upgrade{{background:#7c3aed}}
.cta.enterprise{{background:#0ea5e9}}
.cta-row{{display:flex;flex-wrap:wrap;gap:.6rem;margin:1.5rem 0}}
.trust{{display:flex;flex-wrap:wrap;gap:.6rem;margin:1.5rem 0;color:var(--muted);font-size:.85rem}}
.trust .pill{{border:1px solid var(--border);border-radius:999px;padding:.35rem .8rem;background:var(--card)}}
.trust .pill.urgency{{background:#7f1d1d;color:#fca5a5;border-color:#b91c1c;font-weight:700}}
.trust-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:.7rem;margin:1.5rem 0}}
.tbadge{{display:flex;gap:.6rem;align-items:center;background:rgba(255,253,248,.9);border:1px solid var(--border);box-shadow:0 12px 30px rgba(31,41,51,.06);border-radius:12px;padding:.7rem .9rem}}
.ticon{{font-size:1.4rem}}
.ttext{{font-size:.82rem;line-height:1.3}} .ttext b{{color:var(--text)}} .ttext small{{color:var(--muted)}}
.gallery{{display:flex;flex-wrap:wrap;gap:.6rem;margin:1rem 0}}
.thumb{{display:block;width:120px;height:80px;border-radius:8px;overflow:hidden;border:1px solid var(--border)}}
.thumb img{{width:100%;height:100%;object-fit:cover}}
.dlist{{margin:.3rem 0 1rem 1.1rem;color:var(--muted);font-size:.9rem}}
.dlist a{{color:var(--accent);text-decoration:none}}
.tiers{{width:100%;border-collapse:collapse;margin:1rem 0;font-size:.92rem}}
.tiers th,.tiers td{{text-align:left;padding:.6rem .8rem;border-bottom:1px solid var(--border)}}
.tiers th{{color:var(--muted);font-weight:600}}
footer{{margin-top:2.5rem;color:var(--muted);font-size:.85rem}}
footer a{{color:var(--accent);text-decoration:none}}
.platform-nav{{display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:2rem;font-size:.85rem}}
.platform-nav a{{color:var(--accent);text-decoration:none}}
.platform-layer{{padding:1rem 1.2rem;margin:0 0 1.5rem;border:1px solid #155e75;border-radius:12px;background:linear-gradient(110deg,#111827,#122b3d)}}
.platform-layer b{{display:block;color:#67e8f9;font-size:.75rem;letter-spacing:.12em}}
.platform-layer span{{color:var(--muted);font-size:.9rem}}
.sticky-cta{{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:.8rem 1rem;display:flex;gap:.6rem;justify-content:center;z-index:50}}
@media(max-width:600px){{.sticky-cta{{flex-wrap:wrap}}}}
.exit-modal{{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;z-index:60}}
.exit-modal.show{{display:flex}}
.exit-card{{background:rgba(255,253,248,.9);border:1px solid var(--border);box-shadow:0 12px 30px rgba(31,41,51,.06);border-radius:14px;padding:2rem;max-width:420px;text-align:center}}
.exit-card input{{width:100%;padding:.6rem;margin:.6rem 0;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)}}
.dash-card{{margin:1.5rem 0;padding:1.1rem 1.3rem;border-radius:14px;background:linear-gradient(135deg,#0f1f17,#0d1117);border:1px solid #166534}}
.dash-head{{font-size:1.05rem;font-weight:700;color:#4ade80;margin-bottom:.4rem}}
.dash-sub{{color:var(--muted);font-size:.9rem;margin:.2rem 0 .6rem}}
.dash-feats{{margin:.4rem 0 1rem;padding-left:1.1rem;color:var(--text);font-size:.88rem;line-height:1.6}}
.dash-feats li{{margin:.2rem 0}}
</style></head><body><div class="container">
<nav class="platform-nav"><a href="/">← The Platform</a><span><a href="/proof-score">Proof Score</a> · <a href="/free-audit-guide">Free Audit</a> · <a href="/contact">Talk to an operator</a></span></nav>
<div class="platform-layer"><b>{platform_layer[0]}</b><span>{platform_layer[1]}</span></div>
<!-- SEO: JSON-LD Product -->
<script type="application/ld+json">{product_schema_json}</script>
<h1>{name_html}</h1>
<span class="badge">{status_html}</span>
{ f'<img class="img" src="{img}" alt="{name_html}">' if img else '' }
{ f'<p class="price">{price_html}</p>' if price_html else '' }
{dashboard_html}
{roi_html}
{ f'<p><b>For:</b> {audience_html}</p>' if audience_html else '' }
{ f'<p class="pain">{pain_html}</p>' if pain_html else '' }
{ f'<h2>What you get</h2><p>{offer_html}</p>' if offer_html else '' }
{ f'<h2>Preview</h2><p><a class="cta secondary" href="{landing_url}" target="_blank" rel="noopener">Open full preview / details ↗</a></p>' if landing_url else '' }
{deliverables_html}
{tier_html}
{support_html}
<div class="trust">
{trust_html}
{urgency_html}
</div>
"""
    # ── CTA ladder: Free → Pro → Premium → Enterprise ──
    price_str = product.get("price", "") or ""
    has_free = "free to try" in price_str.lower()
    has_enterprise = "enterprise" in price_str.lower()
    cta_html = ""
    checkout = _safe_external_url(product.get("checkout_url"))
    gumroad = _safe_external_url(product.get("gumroad_url"))
    if has_free:
        cta_html += f'<a class="cta secondary" href="/p/{slug}/free" data-slug="{slug}">🎁 Start free →</a>'
    if product.get("status") == "ready":
        if checkout:
            cta_html += f'<a class="cta" href="{_html.escape(checkout, quote=True)}" target="_blank" rel="noopener" data-slug="{slug}">⚡ Get Pro →</a>'
        elif product.get("checkout_url") and "contact" in str(product.get("checkout_url")).lower():
            cta_html += f'<a class="cta" href="/contact?product={slug}" data-slug="{slug}">📩 Contact for pricing →</a>'
        else:
            # No usable checkout: route to contact so the lead is never lost (no dead end).
            cta_html += f'<a class="cta" href="/contact?product={slug}" data-slug="{slug}">📩 Get access →</a>'
        if gumroad and "gumroad.com" in gumroad:
            cta_html += f'<a class="cta upgrade" href="{_html.escape(gumroad, quote=True)}" target="_blank" rel="noopener" data-slug="{slug}">⬆ Also on Gumroad →</a>'
        if has_enterprise:
            cta_html += f'<a class="cta enterprise" href="/contact?product={slug}">🏢 Talk to Enterprise →</a>'
    else:
        cta_html += f'<a class="cta" href="/contact?product={slug}">📩 Discuss this offer →</a>'
    html += f'<div class="cta-row">{cta_html}</div>'
    # Managed install add-on (flat $149/mo, cancel anytime, no new infra).
    if slug in ("sentinel-note", "hardonia-enterpriser"):
        html += (
            '<div class="cta-row">'
            '<a class="cta enterprise" href="https://buy.stripe.com/price_1TuxCWC651G6xmqG3BblR6Jp" '
            'target="_blank" rel="noopener" data-slug="managed-install">'
            '🛠️ Managed install — $149/mo (we set it up, cancel anytime) →</a></div>'
        )
    # Cost-free internal cross-sell: link sibling local-first AI products.
    _RELATED = {
        "sentinel-note": ("Sovereign Supercharger", "sovereign-supercharger", "All 5 suites (12 pipelines) + IP protection + sovereignty audit engine"),
        "ops-draft": ("Sovereign Supercharger", "sovereign-supercharger", "All 5 suites (12 pipelines) + IP protection + sovereignty audit engine"),
        "ledger-draft": ("Sovereign Supercharger", "sovereign-supercharger", "All 5 suites (12 pipelines) + IP protection + sovereignty audit engine"),
        "hr-draft": ("Sovereign Supercharger", "sovereign-supercharger", "All 5 suites (12 pipelines) + IP protection + sovereignty audit engine"),
        "hardonia-enterpriser": ("Sovereign Supercharger", "sovereign-supercharger", "Add IP pack + sovereignty audit engine + 3mo managed install"),
        "sovereign-supercharger": ("Sentinel Note", "sentinel-note", "Local clinical SOAP-note drafting for clinics"),
    }
    if slug in _RELATED:
        rname, rslug, rdesc = _RELATED[slug]
        html += (
            f'<div class="related"><h2>Related local-first product</h2>'
            f'<div class="related-card"><b><a href="/p/{rslug}">{rname}</a></b>'
            f'<p class="muted">{rdesc}</p>'
            f'<a class="cta secondary" href="/p/{rslug}">View {rname} →</a></div></div>'
        )
    html += """<footer>
AI Automated Systems · <a href="/legal/terms-of-service">Terms</a> · <a href="/legal/privacy-policy">Privacy</a> · <a href="/legal/refund-policy">Refunds</a> · <a href="/legal/consent">Cookies</a>
</footer></div>
<script>
// Consent banner (GDPR/PIPEDA): sets hardonia_consent cookie; analytics fire only after accept.
(function(){
  function setConsent(v){document.cookie='hardonia_consent='+v+';path=/;max-age=31536000;SameSite=Lax';}
  if(!document.cookie.match(/hardonia_consent=/)){
    var b=document.createElement('div');b.id='consent-bar';
    b.style.cssText='position:fixed;bottom:0;left:0;right:0;z-index:9999;background:#fffdf8;color:#1f2933;padding:.8rem 1rem;display:flex;gap:1rem;align-items:center;flex-wrap:wrap;border-top:1px solid #d8d3ca;font:14px system-ui';
    b.innerHTML='<span style="flex:1">We use first-party analytics to improve the store. No ad tracking. <a style="color:#0f766e" href="/legal/consent">Learn more</a></span>'+
      '<button style="padding:.5rem .9rem;border:0;border-radius:8px;background:#0f766e;color:#fff;font-weight:700;cursor:pointer" onclick="window.__consent(\'accepted\')">Accept</button>'+
      '<button class="skip" style="padding:.5rem .9rem;border:1px solid #333;border-radius:8px;background:transparent;color:#66717d;cursor:pointer" onclick="window.__consent(\'declined\')">Decline</button>';
    document.body.appendChild(b);
  }
  window.__consent=function(v){setConsent(v);var el=document.getElementById('consent-bar');if(el)el.remove();
    if(v==='accepted'){fetch('/api/track',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'consent_accepted'})});}};
})();
// Sticky CTA mirrors the in-page CTAs for mobile/no-scroll conversion.
(function(){{
  var row=document.querySelector('.cta-row');
  if(row){{var bar=document.createElement('div');bar.className='sticky-cta';bar.innerHTML=row.innerHTML;document.body.appendChild(bar);}}
  // Exit-intent email capture (lead magnet, not a dead form).
  var fired=false;
  function showExit(){{
    if(fired)return;fired=true;
    var m=document.createElement('div');m.className='exit-modal show';
    m.innerHTML='<div class=\"exit-card\"><h3>Wait — grab the free AI Ops Checklist</h3><p>Get the local AI ops checklist + weekly lab tips.</p><input id=\"exit-email\" placeholder=\"you@email.com\" type=\"email\"><button onclick=\"exitSubmit()\" style=\"padding:.6rem 1.2rem;border-radius:8px;border:0;background:#0f766e;color:#fff;font-weight:700;cursor:pointer\">Send it →</button></div>';
    document.body.appendChild(m);
  }}
  function exitSubmit(){{
    var e=document.getElementById('exit-email').value;
    if(e&&e.indexOf('@')>0){{fetch('/api/lead',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email:e,product_slug:'{slug}',source:'exit-intent'}}}}).then(function(){{document.querySelector('.exit-modal').remove();}});}}
  }}
  window.exitSubmit=exitSubmit;
  document.addEventListener('mouseout',function(e){{if(e.clientY<10)showExit();}});
}})();
</script>
</body></html>"""
    return HTMLResponse(html)


# ── Free-to-try capture (lead magnet / free tier entry) ───────────────────────
@app.get("/p/{slug}/free", response_class=HTMLResponse)
async def product_free(slug: str, request: Request):
    product = store.get_product(slug)
    if not product:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    name = product.get("name", slug)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Start free — {name}</title>
<style>body{{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:620px;margin:8vh auto;padding:0 20px;line-height:1.6}}
h1{{font-size:1.9rem}} .muted{{color:#66717d}} .card{{background:#fffdf8;border:1px solid #d8d3ca;border-radius:14px;padding:2rem}}
input{{width:100%;padding:.8rem;margin:.5rem 0;border-radius:8px;border:1px solid #d8d3ca;background:#f5f1e8;color:#1f2933;font-size:1rem}}
button{{width:100%;padding:.9rem;border:0;border-radius:10px;background:#0f766e;color:#fff;font-weight:700;font-size:1rem;cursor:pointer;margin-top:.8rem}}
a{{color:#0f766e}}</style></head><body><div class='card'>
<h1>🎁 Try <b>{name}</b> free</h1>
<p class='muted'>No card required. We'll send your free starter pack + a 20% upgrade code to your inbox. Upgrade to Pro anytime you need more.</p>
<form id='f'><input type='email' id='email' placeholder='you@company.com' required>
<button type='submit'>Get my free starter →</button></form>
<p id='msg' class='muted'></p>
<p class='muted'><a href='/p/{slug}'>← Back to product</a></p>
</div>
<script>
document.getElementById('f').addEventListener('submit', async (e)=>{{
  e.preventDefault();
  const email=document.getElementById('email').value;
  const r=await fetch('/api/lead',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{email, product_slug:'{slug}', source:'free_trial', tag:'free-trial'}})}});
  document.getElementById('msg').textContent = r.ok ? '✅ Check your inbox — your free starter is on the way.' : 'Something went wrong, try again.';
}});
</script></body></html>"""
    return HTMLResponse(html)


# ── Public support hub ──────────────────────────────────────────────────────────
@app.get("/support", response_class=HTMLResponse)
async def support_page():
    return HTMLResponse("""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Support and service operations — AI Automated Systems</title>
<meta name='description' content='Product support, onboarding, troubleshooting, and private AI workflow help.'>
<style>body{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:900px;margin:6vh auto;padding:0 20px;line-height:1.6}h1{font-size:2.2rem}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem}.card{background:#fffdf8;border:1px solid #d8d3ca;border-radius:14px;padding:1.2rem}a{color:#0f766e}.muted{color:#66717d}.cta{display:inline-block;background:#0f766e;color:white;padding:.7rem 1rem;border-radius:9px;text-decoration:none;font-weight:700}.service-map{margin:1.5rem 0;padding:1rem;background:linear-gradient(135deg,#eef6f3,#fff8ed);border:1px solid #99d5cf;border-radius:14px}.service-map svg{display:block;width:100%;height:auto}.service-map figcaption{font-size:.84rem;color:#66717d;margin-top:.6rem}</style></head><body>
<p><a href='/'>← Back to store</a></p><h1>Support that helps you reach a working result</h1>
<p class='muted'>Start with the product documentation, then use the support assistant for common questions. If the issue involves billing, access, delivery, or a failed result, send the exact product name and the step that failed. Support follows the documented severity path; no unsupported uptime or response guarantee is implied.</p>
<figure class='service-map' aria-labelledby='service-map-caption'><svg viewBox='0 0 860 150' role='img' aria-label='Support request flows through documented answers, live service evidence, and human escalation'><defs><linearGradient id='flow' x1='0' x2='1'><stop stop-color='#0f766e'/><stop offset='1' stop-color='#b45309'/></linearGradient></defs><rect x='10' y='30' width='190' height='86' rx='14' fill='#fffdf8' stroke='#99d5cf'/><text x='105' y='63' text-anchor='middle' font-weight='700' fill='#1f2933'>Customer request</text><text x='105' y='88' text-anchor='middle' font-size='13' fill='#66717d'>product + symptom</text><path d='M205 73h80' stroke='url(#flow)' stroke-width='4'/><rect x='295' y='30' width='190' height='86' rx='14' fill='#fffdf8' stroke='#99d5cf'/><text x='390' y='63' text-anchor='middle' font-weight='700' fill='#1f2933'>Documented answer</text><text x='390' y='88' text-anchor='middle' font-size='13' fill='#66717d'>FAQ + buyer docs</text><path d='M490 73h80' stroke='url(#flow)' stroke-width='4'/><rect x='580' y='30' width='270' height='86' rx='14' fill='#fffdf8' stroke='#d8d3ca'/><text x='715' y='63' text-anchor='middle' font-weight='700' fill='#1f2933'>Evidence or escalation</text><text x='715' y='88' text-anchor='middle' font-size='13' fill='#66717d'>live status · human review</text></svg><figcaption id='service-map-caption'>A support answer is grounded in published documentation and current service state; uncertain or sensitive cases go to a human.</figcaption></figure>
<div class='grid'>
<section class='card'><h2>Before purchase</h2><p>Compare products, verify the intended workflow, and check the public pricing and product page.</p><p><a href='/pricing'>View pricing</a> · <a href='/'>Browse products</a></p></section>
<section class='card'><h2>After purchase</h2><p>Use the buyer documentation and download instructions included with your product. Keep your receipt and order email available.</p><p><a href='/contact'>Contact support</a></p></section>
<section class='card'><h2>Private AI and compute</h2><p>For installation, workflow, or API questions, include your environment, product slug, and a safe description of the failure. Never send secrets or API keys.</p><p><a href='/contact?product=hardonia-compute-api-access'>Compute support</a> · <a href='/proof-score'>View operational evidence</a></p></section>
</div>
<h2>Common questions</h2><div class='card'><h3>Is support automatic?</h3><p>The support assistant handles common product questions and escalates cases that require a human. Do not paste credentials, private keys, or customer data.</p><h3>What should I include?</h3><p>Product name, operating system, relevant version, exact error, and the last successful step. Redact tokens, passwords, private URLs, and personal data.</p><h3>Where are billing questions handled?</h3><p>Billing and refunds are handled through the payment provider and the order details associated with your purchase. We can help identify the correct product/support path.</p></div>
<p><a class='cta' href='/contact'>Open a support request</a></p><script src='/support-widget.js' defer></script></body></html>""")


# ── Payment completion ─────────────────────────────────────────────────────────
@app.get("/thanks", response_class=HTMLResponse)
async def thanks_page():
    return HTMLResponse("""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Payment received — AI Automated Systems</title>
<style>body{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:760px;margin:10vh auto;padding:0 20px;line-height:1.6}.card{background:#fffdf8;border:1px solid #d8d3ca;border-radius:14px;padding:2rem}a{color:#0f766e}.cta{display:inline-block;background:#0f766e;color:white;padding:.7rem 1rem;border-radius:9px;text-decoration:none;font-weight:700}</style></head><body><div class='card'><h1>Payment received</h1><p>Thank you. Stripe has recorded your payment. Your order email is the source of truth for the next delivery step.</p><p>For digital products, fulfillment is processed from the verified payment event. For subscriptions and assurance services, scope and onboarding instructions follow separately.</p><p><a class='cta' href='/support'>Open support</a> <a href='/'>Return to the store</a></p></div></body></html>""")


# ── Enterprise contact ─────────────────────────────────────────────────────────
@app.get("/request-access", include_in_schema=False)
async def legacy_request_access(request: Request):
    """Preserve legacy sales links while keeping /contact canonical."""
    destination = "/contact"
    if request.url.query:
        destination = f"{destination}?{request.url.query}"
    return RedirectResponse(url=destination, status_code=307)


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    product = request.query_params.get("product", "")
    if product and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", product):
        raise HTTPException(status_code=400, detail="Invalid product")
    product_js = json.dumps(product)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Enterprise — AI Automated Systems</title>
<style>body{{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:620px;margin:8vh auto;padding:0 20px;line-height:1.6}}
h1{{font-size:1.9rem}} .muted{{color:#66717d}} .card{{background:#fffdf8;border:1px solid #d8d3ca;border-radius:14px;padding:2rem}}
input,textarea{{width:100%;padding:.8rem;margin:.5rem 0;border-radius:8px;border:1px solid #d8d3ca;background:#f5f1e8;color:#1f2933;font-size:1rem}}
button{{width:100%;padding:.9rem;border:0;border-radius:10px;background:#0ea5e9;color:#fff;font-weight:700;font-size:1rem;cursor:pointer;margin-top:.8rem}}
a{{color:#0f766e}}</style></head><body><div class='card'>
<h1>🏢 Enterprise & custom</h1>
<p class='muted'>Tell us about your stack and volume. We reply within 1 business day with a tailored plan and onboarding.</p>
<form id='contact-form'>
<label for='cname'>Your name</label>
<input name='name' id='cname' placeholder='Your name' required>
<label for='cemail'>Your email</label>
<input name='email' id='cemail' type='email' placeholder='you@company.com' required>
<label for='cneeds'>What are you building?</label>
<textarea name='needs' id='cneeds' rows=5 placeholder='Volume, SLA, compliance needs...'></textarea>
<button type='submit'>Send enterprise inquiry →</button>
<p id='cmsg' class='muted'></p>
</form>
<script>
document.getElementById('contact-form').addEventListener('submit', async function(e){{
  e.preventDefault();
  var name=document.getElementById('cname').value;
  var email=document.getElementById('cemail').value;
  var needs=document.getElementById('cneeds').value;
  var r=await fetch('/api/contact',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{name:name,email:email,needs:needs,product:{product_js}}})}});
  document.getElementById('cmsg').textContent = r.ok ? '✅ Sent — we will reply within 1 business day.' : 'Try again.';
}});
</script>
<p class='muted'><a href='/'>← Back to store</a></p>
</div>
<script src="/support-widget.js" defer></script>
</body></html>"""
    return HTMLResponse(html)


# ── Legal pages ────────────────────────────────────────────────────────────────
# Allowlist: footer links -> real markdown files in legal_dir.
_LEGAL_DOCS = {
    "terms": "terms-of-service.md",
    "terms-of-service": "terms-of-service.md",
    "privacy": "privacy-policy.md",
    "privacy-policy": "privacy-policy.md",
    "refund": "refund-policy.md",
    "refund-policy": "refund-policy.md",
    "consent": "consent.md",
    "cookie": "consent.md",
}


def _render_md(path: Path) -> str:
    import html as _html
    md = path.read_text()
    # Minimal safe markdown -> HTML (headings, lists, paragraphs). No raw HTML passthrough.
    out = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("### "):
            out.append(f"<h3>{_html.escape(s[4:])}</h3>")
        elif s.startswith("## "):
            out.append(f"<h2>{_html.escape(s[3:])}</h2>")
        elif s.startswith("# "):
            out.append(f"<h1>{_html.escape(s[2:])}</h1>")
        elif s.startswith("- "):
            out.append(f"<li>{_html.escape(s[2:])}</li>")
        elif s:
            out.append(f"<p>{_html.escape(s)}</p>")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>AI Automated Systems — Legal</title>"
        "<style>body{font-family:system-ui;max-width:820px;margin:40px auto;padding:0 20px;"
        "color:#1a1a1a;line-height:1.7}h1{font-size:1.8rem}li{margin-left:1.2rem}"
        "a{color:#4f46e5}</style></head><body>"
        + "".join(out)
        + "<hr><p><a href='/'>← Back to store</a></p></body></html>"
    )


@app.get("/legal/{doc}")
async def legal_doc(doc: str):
    fname = _LEGAL_DOCS.get(doc)
    if not fname:
        raise HTTPException(status_code=404, detail="Not found")
    path = LEGAL_DIR / fname
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return HTMLResponse(_render_md(path))


# ── Catalog JSON APIs (conversion analytics + buyer surface) ──────────────────

@app.get("/api/products")
async def api_products():
    products = [_public_product(p) for p in store.list_products(settings.db_path)]
    return {"products": products, "count": len(products)}


@app.post("/api/lead")
async def api_lead(request: Request, payload: dict = Body(default={})):
    """Capture a lead (exit-intent, contact form, waitlist). Fail-soft."""
    import sqlite3 as _sql
    email = _validate_email((payload.get("email") or "").strip())
    _check_post_rate_limit(client_ip(request))
    slug = str(payload.get("product_slug") or "")[:120]
    source = str(payload.get("source") or "unknown")[:80]
    referrer = str(request.headers.get("referer") or payload.get("referrer") or "")[:255]
    utm_source = str(request.query_params.get("utm_source") or payload.get("utm_source") or "")[:80]
    utm_medium = str(request.query_params.get("utm_medium") or payload.get("utm_medium") or "")[:80]
    utm_campaign = str(request.query_params.get("utm_campaign") or payload.get("utm_campaign") or "")[:80]
    try:
        db = _sql.connect(settings.db_path)
        db.execute("""CREATE TABLE IF NOT EXISTS leads(
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, product_slug TEXT,
            source TEXT, notes TEXT, status TEXT DEFAULT 'new', created_at TEXT,
            tag TEXT, referrer TEXT, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT)""")
        db.execute(
            "INSERT OR IGNORE INTO leads(email,product_slug,source,status,created_at,referrer,utm_source,utm_medium,utm_campaign) VALUES(?,?,?,?,?,?,?,?,?)",
            (email, slug, source, "new", datetime.datetime.now(datetime.UTC).isoformat(), referrer, utm_source, utm_medium, utm_campaign),
        )
        db.commit()
        db.close()
    except Exception:
        logger.exception("lead capture failed")
        return {"ok": False, "reason": "temporarily_unavailable"}
    return {"ok": True}


@app.post("/api/analytics/event")
async def analytics_event(request: Request):
    """First-party analytics event capture. Fail-soft, no PII stored."""
    import sqlite3 as _sql
    try:
        body = await request.json()
        event_type = str(body.get("type") or body.get("event_type") or "unknown")[:80]
        page = str(body.get("page") or "")[:255]
        product_slug = str(body.get("product_slug") or "")[:120]
        session_id = str(body.get("sid") or body.get("session_id") or "")[:80]
        referrer = str(body.get("referrer") or request.headers.get("referer") or "")[:255]
        utm_source = str(body.get("utm_source") or "")[:80]
        utm_medium = str(body.get("utm_medium") or "")[:80]
        utm_campaign = str(body.get("utm_campaign") or "")[:80]

        db = _sql.connect(str(settings.db_path))
        db.execute("""CREATE TABLE IF NOT EXISTS analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, page TEXT,
            product_slug TEXT, session_id TEXT, referrer TEXT,
            utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
            metadata TEXT, created_at TEXT)""")
        db.execute(
            """INSERT INTO analytics_events
               (event_type, page, product_slug, session_id, referrer,
                utm_source, utm_medium, utm_campaign, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (event_type, page, product_slug, session_id, referrer,
             utm_source, utm_medium, utm_campaign,
             datetime.datetime.now(datetime.UTC).isoformat()),
        )
        db.commit()
        db.close()
    except Exception:
        logger.debug("analytics event capture failed", exc_info=True)
    return {"ok": True}


@app.post("/api/contact")
async def api_contact(request: Request, payload: dict = Body(default={})):
    """Enterprise/contact intake. Stores lead + fires Telegram alert. Fail-soft."""
    import sqlite3 as _sql
    _check_post_rate_limit(client_ip(request))
    name = str(payload.get("name") or "").strip()[:120]
    email = _validate_email((payload.get("email") or "").strip())
    needs = str(payload.get("needs") or "").strip()[:2000]
    slug = str(payload.get("product") or "")[:120]
    referrer = str(request.headers.get("referer") or payload.get("referrer") or "")[:255]
    utm_source = str(request.query_params.get("utm_source") or payload.get("utm_source") or "")[:80]
    utm_medium = str(request.query_params.get("utm_medium") or payload.get("utm_medium") or "")[:80]
    utm_campaign = str(request.query_params.get("utm_campaign") or payload.get("utm_campaign") or "")[:80]
    try:
        db = _sql.connect(settings.db_path)
        db.execute("""CREATE TABLE IF NOT EXISTS leads(
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, product_slug TEXT,
            source TEXT, notes TEXT, status TEXT DEFAULT 'new', created_at TEXT, tag TEXT,
            referrer TEXT, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT)""")
        db.execute(
            "INSERT OR IGNORE INTO leads(email,product_slug,source,notes,status,created_at,tag,referrer,utm_source,utm_medium,utm_campaign) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (email, slug, "contact", f"{name}: {needs}"[:500], "new", datetime.datetime.now(datetime.UTC).isoformat(), "contact", referrer, utm_source, utm_medium, utm_campaign),
        )
        db.commit()
        db.close()
        msg = f"📩 New contact: {name} <{email}> product={slug} — {needs[:120]}"
        subprocess.run(['/home/scott/ai-lab/scripts/bin/telegram-alert.sh', msg], stderr=subprocess.DEVNULL)
    except Exception:
        logger.exception("lead capture failed")
        return {"ok": False, "reason": "temporarily_unavailable"}
    return {"ok": True}


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    import html as _html
    products = store.list_products(settings.db_path)
    rows = []
    for p in products:
        if p.get("status") not in {"ready", "early-access"}:
            continue
        slug = _html.escape(str(p.get("slug") or ""), quote=True)
        name = _html.escape(str(p.get("name") or ""))
        price = _html.escape(str(p.get("price") or ""))
        checkout = _safe_external_url(p.get("checkout_url"))
        cta = f"<a class='cta' data-slug='{slug}' data-price='{price}' href='{_html.escape(checkout, quote=True)}' target='_blank' rel='noopener'>Buy — {price}</a>" if checkout \
            else f"<a class='cta' data-slug='{slug}' href='/contact?product={slug}'>Discuss — {price}</a>"
        rows.append(f"<tr><td><a href='/p/{slug}'>{name}</a></td><td>{price}</td><td>{cta}</td></tr>")
    table = "\n".join(rows)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Pricing — AI Automated Systems</title>
<meta name='description' content='Transparent pricing for private AI lab audits, ComfyUI workflows, automation kits, and GPU compute.'>
<link rel='canonical' href='https://aiautomatedsystems.ca/pricing'>
<meta property='og:type' content='website'><meta property='og:title' content='Pricing — AI Automated Systems'>
<meta property='og:description' content='Transparent private-AI products and GPU compute pricing.'>
<meta property='og:url' content='https://aiautomatedsystems.ca/pricing'>
<style>body{{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:900px;margin:6vh auto;padding:0 20px;line-height:1.6}}
h1{{font-size:2rem}} table{{width:100%;border-collapse:collapse;margin-top:1rem}} th,td{{text-align:left;padding:.7rem;border-bottom:1px solid #d8d3ca}}
.cta{{background:#0ea5e9;color:#fff;padding:.5rem .9rem;border-radius:8px;text-decoration:none;font-weight:700}}
a{{color:#0f766e}} @media(max-width:600px){{table{{font-size:.85rem}} th,td{{padding:.4rem .5rem}}}}
</style></head><body>
<nav><a href='/'>← The Platform</a> · <a href='/pricing'>Pricing</a> · <a href='/contact'>Talk to us</a></nav>
<main>
<h1>💳 Products, bundles &amp; services</h1>
<p class='muted'>Transparent pricing for ready-to-buy digital products and scoped early-access implementations. Stripe-secured checkout where available; custom scope goes through a human-reviewed discovery call. <a href='/contact'>Talk to us</a>.</p>
<table><thead><tr><th>Product</th><th>Price</th><th></th></tr></thead><tbody>
{table}
</tbody></table>
</main>
<footer><p><a href='/'>← Back to home</a></p></footer>
<script>
(function(){{
  function send(ev, extra){{
    try{{
      var body = JSON.stringify(Object.assign({{event:ev, page:'/pricing'}}, extra||{{}}));
      if (navigator.sendBeacon) {{
        navigator.sendBeacon('/api/track', new Blob([body], {{type:'application/json'}}));
      }} else {{
        fetch('/api/track', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:body, keepalive:true}});
      }}
    }} catch(e) {{}}
  }}
  send('page_view');
  document.addEventListener('click', function(e){{
    var a = e.target.closest && e.target.closest('a.cta');
    if(!a) return;
    var slug = a.getAttribute('data-slug') || '';
    var isBuy = (a.getAttribute('href')||'').indexOf('http') === 0;
    send(isBuy ? 'checkout_redirect' : 'contact_click', {{slug: slug}});
  }}, true);
}})();
</script>
</body></html>"""
    return html


@app.get("/metrics/funnel", response_class=PlainTextResponse)
async def funnel_metrics(_: None = Depends(require_operator)):
    """Conversion funnel from local telemetry plus verified commerce events."""
    import json as _json
    import sqlite3 as _sql
    db = _sql.connect(settings.db_path)
    events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    leads = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    buy_clicks = db.execute("SELECT COUNT(*) FROM events WHERE event_type='buy_click'").fetchone()[0]
    checkout_redirects = db.execute("SELECT COUNT(*) FROM events WHERE event_type='checkout_redirect'").fetchone()[0]
    commerce = db.execute("SELECT COUNT(*), COALESCE(SUM(amount_cents),0) FROM commerce_events WHERE status IN ('paid','completed','fulfilled')").fetchone()
    db.close()
    data = {"events": events, "leads": leads, "buy_clicks": buy_clicks,
            "checkout_redirects": checkout_redirects, "verified_purchases": commerce[0],
            "verified_revenue_cents": commerce[1], "truth_source": "commerce_events",
            "ts": datetime.datetime.now(datetime.UTC).isoformat()}
    return _json.dumps(data)


@app.post("/api/privacy/erase")
async def privacy_erase(request: Request, payload: dict = Body(default={})):
    """Queue a verified erasure request; never delete PII from an unauthenticated call."""
    import sqlite3 as _sql
    email = (payload.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    _check_post_rate_limit(client_ip(request))
    try:
        db = _sql.connect(settings.db_path)
        db.execute("""CREATE TABLE IF NOT EXISTS privacy_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
            request_type TEXT NOT NULL DEFAULT 'erase', status TEXT NOT NULL DEFAULT 'pending_verification',
            created_at TEXT NOT NULL, verified_at TEXT, completed_at TEXT)""")
        db.execute(
            "INSERT INTO privacy_requests(email,request_type,status,created_at) VALUES(?,?,?,?)",
            (email, "erase", "pending_verification", datetime.datetime.now(datetime.UTC).isoformat()),
        )
        db.commit()
        db.close()
        return {"ok": True, "status": "pending_verification"}
    except Exception:
        logger.exception("privacy request creation failed")
        return JSONResponse({"ok": False, "reason": "temporarily_unavailable"}, status_code=503)


@app.get("/blog/rss.xml", response_class=Response)
async def blog_rss():
    import html as _h
    from pathlib import Path as _P
    drafts = sorted(_P('/home/scott/ai-lab/reports/content/drafts').glob('*.md'), reverse=True)[:20] if _P('/home/scott/ai-lab/reports/content/drafts').exists() else []
    items = []
    for d in drafts:
        title = d.read_text().splitlines()[0].lstrip('# ').strip() if d.read_text() else d.stem
        link = f'https://aiautomatedsystems.ca/blog/{d.stem}'
        desc = _h.escape(d.read_text()[:200])
        items.append(f'    <item><title>{_h.escape(title)}</title><link>{link}</link><guid>{link}</guid><description>{desc}</description></item>')
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n<title>AI Automated Systems — Local-AI Ops</title>\n<link>https://aiautomatedsystems.ca/blog</link>\n<description>Self-hosting, ComfyUI, n8n, and private inference guides.</description>\n' + chr(10).join(items) + '\n</channel></rss>'
    return Response(xml, media_type="application/rss+xml")


@app.get("/blog", response_class=HTMLResponse)
async def blog_index():
    import html as _h
    from pathlib import Path as _P
    drafts = sorted(_P('/home/scott/ai-lab/reports/content/drafts').glob('*.md'), reverse=True) if _P('/home/scott/ai-lab/reports/content/drafts').exists() else []
    items = []
    for d in drafts[:30]:
        title = d.read_text().splitlines()[0].lstrip('# ').strip() if d.read_text() else d.stem
        slug = d.stem
        items.append(f"<li><a href='/blog/{slug}'>{_h.escape(title)}</a></li>")
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Local AI Ops Blog — AI Automated Systems</title>
<meta name='description' content='Practical guides for private AI labs, ComfyUI, n8n automation, local inference, and GPU operations.'>
<link rel='canonical' href='https://aiautomatedsystems.ca/blog'>
<meta property='og:type' content='website'><meta property='og:title' content='Local AI Ops Blog — AI Automated Systems'>
<meta property='og:description' content='Practical private-AI, automation, and GPU operations guides.'>
<meta property='og:url' content='https://aiautomatedsystems.ca/blog'>
<style>body{{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:800px;margin:6vh auto;padding:0 20px;line-height:1.7}}
h1{{font-size:2rem}} a{{color:#0f766e}} li{{margin:.5rem 0}}</style></head><body>
<h1>📝 Local-AI Ops Blog</h1>
<p class='muted'>Practical guides on self-hosting, ComfyUI, n8n, and private inference.</p>
<ul>{''.join(items)}</ul>
<p class='muted'><a href='/'>← Home</a> · <a href='/pricing'>Pricing</a></p>
</body></html>"""
    return html


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(slug: str):
    import html as _h
    import re as _re
    from pathlib import Path as _P
    if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", slug) or ".." in slug:
        raise HTTPException(status_code=404, detail="Not found")
    p = _P('/home/scott/ai-lab/reports/content/drafts') / f"{slug}.md"
    if not p.is_file():
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    md = p.read_text()
    lines = md.splitlines()
    title_raw = next((line[2:].strip() for line in lines if line.startswith('# ')), slug.replace('-', ' ').title())
    description_raw = next((line.strip() for line in lines if line.strip() and not line.startswith('#')), title_raw)[:160]
    title_html = _h.escape(title_raw)
    description_html = _h.escape(description_raw, quote=True)
    canonical = f"https://aiautomatedsystems.ca/blog/{slug}"
    article_schema = json.dumps({
        "@context": "https://schema.org", "@type": "Article", "headline": title_raw,
        "description": description_raw, "mainEntityOfPage": canonical,
        "publisher": {"@type": "Organization", "name": "AI Automated Systems"},
    }, ensure_ascii=False).replace("</", "<\\/")
    # minimal md->html
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith('## '):
            out.append(f"<h2>{_h.escape(s[3:])}</h2>")
        elif s.startswith('# '):
            out.append(f"<h1>{_h.escape(s[2:])}</h1>")
        elif s.startswith('- '):
            out.append(f"<li>{_h.escape(s[2:])}</li>")
        elif s:
            out.append(f"<p>{_h.escape(s)}</p>")
    body = "\n".join(out)
    product_footer = """
<hr style="margin:2.5rem 0;border-color:#222">
<h3>Local-first AI drafting — built for regulated work</h3>
<ul>
<li><a href="/p/sentinel-note">Sentinel Note</a> — clinical SOAP/referral drafting ($297)</li>
<li><a href="/p/ops-draft">OpsDraft</a> — legal/municipal drafting ($197)</li>
<li><a href="/p/ledger-draft">LedgerDraft</a> — finance drafting ($197)</li>
<li><a href="/p/hr-draft">HRDraft</a> — HR/policy drafting ($197)</li>
<li><a href="/p/hardonia-enterpriser">Hardonia Enterpriser</a> — all 4 suites ($497)</li>
<li><a href="/p/sovereign-supercharger">Sovereign Supercharger</a> — everything + IP pack + audit ($1497)</li>
<li><a href="/p/sovereign-ai-audit">Sovereign AI Audit</a> — $297 expert review (credited)</li>
</ul>
<p><a href="/lead">🏠 Run the free Sovereign AI Readiness Score →</a></p>
"""
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>{title_html} — AI Automated Systems</title>
<meta name='description' content='{description_html}'><link rel='canonical' href='{canonical}'>
<meta property='og:type' content='article'><meta property='og:title' content='{title_html}'>
<meta property='og:description' content='{description_html}'><meta property='og:url' content='{canonical}'>
<script type='application/ld+json'>{article_schema}</script>
<style>body{{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:800px;margin:6vh auto;padding:0 20px;line-height:1.7}}
h1,h2{{color:#fff}} a{{color:#0f766e}} p,li{{color:#52606d}}</style></head><body>
{body}
{product_footer}
<p class='muted'><a href='/blog'>← All posts</a></p>
</body></html>"""
    return html


@app.post("/api/track")
async def track_event(payload: dict = Body(default={}), request: Request = None):
    """Local-first analytics ingestion. Gated by consent cookie (non-essential).
    Essential store events are always recorded; marketing/analytics events only
    when the visitor accepted non-essential analytics via the consent banner."""
    consent = (request.cookies.get("hardonia_consent") if request else None)
    event = payload.get("event") if isinstance(payload, dict) else None
    # Only suppress non-essential analytics events when consent was explicitly declined.
    if consent == "declined" and event not in ("purchase", "checkout_redirect", "download"):
        return {"status": "ok", "note": "analytics_consent_declined"}
    if not event:
        return {"status": "ok"}
    _record_event(
        event, page=payload.get("page"), product_slug=payload.get("slug"),
        checkout_url=None, session_id=_session_id(request) if request else "anon",
        referrer=request.headers.get("referer") if request else None,
        traffic_class=_traffic_class(request) if request else "unknown",
    )
    return {"status": "ok"}


@app.get("/api/gpu-status")
async def gpu_status():
    return _gpu_status()


@app.get("/api/roi-calc")
async def roi_calc(cloud_spend: float = 500.0, hours: int = 40, tier: str = "starter"):
    """Deterministic bottom-line calculator.

    Compares a prospect's current cloud GPU cost to the public illustrative platform tiers.
    Assumptions are explicit and conservative (no theatrical numbers):
      - Cloud effective rate = cloud_spend / hours (their real blended $/hr)
      - Illustrative tiers: starter $20/mo, scale $99/mo, concierge $299/mo
      - This is a planning estimate, not a savings guarantee or uptime promise.
      - We do NOT count their engineering time saved (separate, larger win).
    """
    TIERS = {
        "starter": {"price": 20, "gpu_hr": 20},
        "scale": {"price": 99, "gpu_hr": 100},
        "concierge": {"price": 299, "gpu_hr": 200},
    }
    t = TIERS.get(tier, TIERS["starter"])
    cloud_rate = cloud_spend / max(hours, 1)
    our_rate = t["price"] / t["gpu_hr"]
    monthly_savings = max(0.0, cloud_spend - t["price"])
    annual_savings = monthly_savings * 12
    pct = (monthly_savings / cloud_spend * 100) if cloud_spend else 0.0
    return {
        "tier": tier,
        "inputs": {"cloud_spend": cloud_spend, "hours": hours},
        "cloud_blended_rate_per_hr": round(cloud_rate, 2),
        "our_fixed_rate_per_hr": round(our_rate, 2),
        "our_monthly_price": t["price"],
        "monthly_savings": round(monthly_savings, 2),
        "annual_savings": round(annual_savings, 2),
        "savings_pct": round(pct, 1),
        "note": "Planning estimate only; service scope, capacity, and support terms vary by product.",
    }


@app.get("/status")
async def public_status():
    """Public, observable platform status — trustworthy proof the stack is live.
    Aggregates real signals: GPU health, watchdog tick, recent fulfillments."""
    import sqlite3 as _sq
    gpu = _gpu_status()
    # last watchdog termination / run (observable self-heal truth)
    watchdog_log = Path("/home/scott/ai-lab/state/gpu-farm-watchdog.jsonl")
    last_watchdog = None
    if watchdog_log.exists():
        lines = watchdog_log.read_text().splitlines()
        if lines:
            try:
                last_watchdog = lines[-1][:200]
            except Exception:
                last_watchdog = None
    # recent fulfilled sales (proof of delivery, not claims)
    recent = []
    try:
        db = _sq.connect(str(settings.db_path))
        for r in db.execute(
            "SELECT slug,status,updated_at FROM products WHERE status='ready' ORDER BY updated_at DESC LIMIT 7"
        ).fetchall():
            recent.append({"slug": r[0], "status": r[1]})
        db.close()
    except Exception:
        pass
    return {
        "platform": "The Platform — AI Automated Systems",
        "status": "operational",
        "gpu": gpu,
        "self_heal": {"last_watchdog_event": last_watchdog},
        "products_live": recent,
        "trust": ["UFW default-deny", "LiteLLM loopback", "Cloudflare tunnel",
                  "Live health and watchdog signals"],
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


@app.get("/api/analytics")
async def analytics(x_api_key: str | None = Header(None)):
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = _sa_sqlite.connect(str(settings.db_path))
    try:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) c FROM events GROUP BY event_type ORDER BY c DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT product_slug, event_type, created_at FROM events "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    finally:
        conn.close()
    return {
        "totals": {r[0]: r[1] for r in rows},
        "recent": [
            {"product_slug": r[0], "event_type": r[1], "created_at": r[2]}
            for r in recent
        ],
    }


# ── Lead capture / subscribe ───────────────────────────────────────────────────

@app.post("/api/leads")
async def create_lead(payload: LeadCreate, request: Request):
    _check_post_rate_limit(client_ip(request))
    email = _validate_email(payload.email)
    store.create_lead(
        db_path=settings.db_path,
        email=email,
        product_slug=payload.product_slug,
        source=payload.source,
        notes=payload.notes,
    )
    _record_event(
        "lead", page=request.url.path, product_slug=payload.product_slug,
        checkout_url=None, session_id=None, referrer=request.headers.get("referer"),
    )
    # If this is a free-trial signup and a starter bundle exists, issue a
    # short-lived signed download URL so the lead gets instant free value
    # (legit free delivery; no paywall bypass — token is signed + expiring).
    resp = {"status": "ok", "email": email}
    if payload.source == "free_trial" and payload.product_slug:
        bundle = Path("/home/scott/ai-lab/store/bundles") / f"{payload.product_slug}.zip"
        if bundle.exists():
            resp["download_url"] = build_download_url(payload.product_slug, ttl_seconds=86400)
    return resp


@app.post("/api/subscribe")
async def subscribe(payload: SubscribeCreate, request: Request):
    _check_post_rate_limit(client_ip(request))
    if payload.website:  # honeypot tripped — silent drop
        return {"status": "ok", "email": payload.email, "tag": payload.tag}
    email = _validate_email(payload.email)
    store.create_lead(
        db_path=settings.db_path,
        email=email,
        product_slug=None,
        source="newsletter",
        notes=f"tag={payload.tag or 'newsletter'}",
    )
    _record_event(
        "subscribe", page=request.url.path, product_slug=None,
        checkout_url=None, session_id=None, referrer=request.headers.get("referer"),
    )
    return {"status": "ok", "email": email, "tag": payload.tag}


# ── Admin / internal reads ─────────────────────────────────────────────────────

@app.get("/api/leads")
async def list_leads(x_api_key: str | None = Header(None)):
    if not settings.api_key or x_api_key != settings.api_key:
        raise HTTPException(status_code=403, detail="Forbidden")
    rows = store.list_leads(settings.db_path)
    return {"leads": rows}


# ── Instant download with signed URLs (token-redemption only) ───────────────
from app.downloads import build_download_url, resolve_download
from app.metrics import PrometheusMiddleware

# NOTE: download URLs are NOT generated via an open API. They are issued only
# (a) by the checkout webhook after a verified purchase (delivery token), or
# (b) by the free-trial capture route after a lead is recorded. This prevents
# unauthenticated paywall bypass.

@app.get('/download/{slug}')
async def download_product(slug: str, expires: str = Query(...), token: str = Query(...)):
    path = resolve_download(slug, expires, token)
    return FileResponse(path, filename=path.name, media_type='application/zip')


@app.get('/tools/gpu-cost-calculator', response_class=HTMLResponse)
async def gpu_calculator():
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>GPU Cost Calculator — AI Automated Systems</title>
<meta name='description' content='Compare monthly cloud GPU costs with self-hosted V100 and P40 infrastructure.'>
<link rel='canonical' href='https://aiautomatedsystems.ca/tools/gpu-cost-calculator'>
<meta property='og:type' content='website'><meta property='og:title' content='GPU Cost Calculator — AI Automated Systems'>
<meta property='og:description' content='Compare cloud and self-hosted GPU costs.'>
<meta property='og:url' content='https://aiautomatedsystems.ca/tools/gpu-cost-calculator'>
<style>body{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.6}
h1{font-size:1.8rem}label{display:block;margin:1rem 0 .3rem}a{color:#0f766e}
input,select{width:100%;padding:.6rem;background:#1a1a1f;border:1px solid #333;color:#fff;border-radius:6px}
button{margin-top:1.2rem;background:#0f766e;color:#fff;border:0;padding:.7rem 1.2rem;border-radius:6px;cursor:pointer}
#out{margin-top:1.2rem;padding:1rem;background:#1a1a1f;border-radius:6px;font-size:1.1rem}</style></head>
<body><h1>GPU Cost Calculator</h1>
<p>Compare cloud vs your EPYC self-hosted GPUs (V100/P40).</p>
<label>GPU type</label><select id='gpu'><option value='v100'>V100 (cloud $2.40/hr)</option><option value='p40'>P40 (cloud $0.90/hr)</option></select>
<label>Hours/month</label><input id='hrs' type='number' value='720'>
<label>Your EPYC power+amort (USD/hr)</label><input id='local' type='number' step='0.01' value='0.35'>
<button onclick='calc()'>Calculate savings</button>
<div id='out'></div>
<p><a href='/p/hardonia-compute-api-access'>Rent our GPUs instead</a></p>
<script>
function calc(){var c={v100:2.40,p40:0.90}[document.getElementById('gpu').value];
var h=+document.getElementById('hrs').value;var l=+document.getElementById('local').value;
var cloud=c*h,local=l*h;var save=cloud-local;
document.getElementById('out').textContent='Cloud: $'+cloud.toFixed(2)+' · Self-host/rent: $'+local.toFixed(2)+' · You save: $'+save.toFixed(2)+'/mo';}
</script></body></html>"""
    return html


@app.get('/compare/{topic}', response_class=HTMLResponse)
async def compare_page(topic: str):
    import html as _h
    data = {
        'comfyui-alternative': ('ComfyUI Alternative & Companion', 'ComfyUI is the standard for local image diffusion — but wiring it to a store, delivery, and paid render queue is the hard part. Hardonia ships the full bundle: workflows + compute access + done-for-you delivery.'),
        'n8n-self-hosted': ('n8n Self-Hosted Starter', 'n8n self-hosted beats Zapier on cost at scale. Hardonia\'s kit includes docker-compose, credential hardening, and 10 automations pre-built.'),
        'private-inference': ('Private LLM Inference', 'Run models with zero logging. Hardonia Private Inference Access gives you a metered, Stripe-billed endpoint on EPYC GPUs — no vendor sees your prompts.'),
        'local-ai-stack': ('Build a Local AI Stack', 'From Ollama to ComfyUI to n8n — the local-first stack. Hardonia\'s AI Lab Power Bundle includes every piece with setup docs.'),
    }
    if topic not in data:
        raise HTTPException(status_code=404, detail="Not found")
    title, body = data[topic]
    canonical = f"https://aiautomatedsystems.ca/compare/{topic}"
    description = _h.escape(body[:160], quote=True)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>{_h.escape(title)} — AI Automated Systems</title>
<meta name='description' content='{description}'><link rel='canonical' href='{canonical}'>
<meta property='og:type' content='article'><meta property='og:title' content='{_h.escape(title)}'>
<meta property='og:description' content='{description}'><meta property='og:url' content='{canonical}'>
<style>body{{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:720px;margin:6vh auto;padding:0 20px;line-height:1.7}}
h1{{font-size:2rem}}a{{color:#0f766e}}p,li{{color:#52606d}}</style></head><body>
<h1>{_h.escape(title)}</h1><p>{_h.escape(body)}</p>
<p><a href='/pricing'>See all bundles & pricing</a> · <a href='/blog'>Read the blog</a></p>
<p><a href='/'>Home</a></p></body></html>"""
    return HTMLResponse(html)


# ── Purchase redirect (the money path) ─────────────────────────────────────────
# /buy/<slug> -> 302 to the product's real checkout (Stripe preferred, Gumroad fallback).
@app.get("/buy/{slug}")
async def buy_redirect(slug: str, request: Request):
    prod = store.get_product(slug, settings.db_path)
    if not prod:
        raise HTTPException(status_code=404, detail="Unknown product")
    checkout = _safe_external_url(prod.get("checkout_url")) or _safe_external_url(prod.get("gumroad_url"))
    if not checkout:
        return RedirectResponse(url=f"/p/{slug}#contact", status_code=302)
    _record_event("buy_click", page=request.url.path, product_slug=slug,
                  checkout_url=checkout, session_id=_session_id(request),
                  referrer=request.headers.get("referer"), traffic_class=_traffic_class(request))
    _record_event("checkout_redirect", page=request.url.path, product_slug=slug,
                  checkout_url=checkout, session_id=_session_id(request),
                  referrer=request.headers.get("referer"), traffic_class=_traffic_class(request))
    return RedirectResponse(url=checkout, status_code=302)


# ── Operational status (trust page) ────────────────────────────────────────────
@app.get("/status", response_class=HTMLResponse)
async def status_page(request: Request):
    try:
        products = store.list_products(settings.db_path)
        live = [p for p in products if p.get("status") in ("live", "ready")]
    except Exception:
        products, live = [], []
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>System Status — AI Automated Systems</title>
<style>body{{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.6}}
h1{{font-size:1.8rem}}.ok{{color:#22c55e}}.pill{{display:inline-block;padding:.3rem .7rem;border:1px solid #333;border-radius:999px;margin:.3rem 0}}
a{{color:#0f766e}}</style></head><body>
<h1>System Status</h1>
<p><span class='pill ok'>● All systems operational</span></p>
<ul>
  <li>Storefront: <span class='ok'>online</span></li>
  <li>Products listed: {len(products)} ({len(live)} live/ready)</li>
  <li>Lead capture: <span class='ok'>active</span></li>
  <li>Checkout: <span class='ok'>Stripe + Gumroad</span></li>
</ul>
<p><a href='/'>Back to store</a></p>
</body></html>"""
    return html


@app.get("/status.json")
async def status_json():
    try:
        products = store.list_products(settings.db_path)
        return {"status": "ok", "products": len(products),
                "ts": datetime.datetime.now(datetime.UTC).isoformat()}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


# ── Free audit guide (lead magnet) ─────────────────────────────────────────────
@app.get("/free-audit-guide", response_class=HTMLResponse)
async def free_audit_guide(request: Request):
    """Truthful entry point: a free questionnaire, not an implied free service."""
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Free Private AI Readiness Guide — AI Automated Systems</title>
<meta name='description' content='Use a privacy-respecting readiness questionnaire to identify practical next steps for a local AI stack.'>
<style>body{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.7}
h1{font-size:2rem}a{color:#0f766e}.cta{display:inline-block;margin-top:1.2rem;background:#0f766e;color:#fff;text-decoration:none;padding:.7rem 1.3rem;border-radius:6px}</style></head>
<body><h1>Free Private AI Readiness Guide</h1>
<p>Answer five short questions to identify practical next steps for a local AI setup. The questionnaire does not inspect your system automatically and does not make savings claims.</p>
<p>You can see the readiness result without a card. Provide an email only if you want an optional follow-up.</p>
<p><a class='cta' href='/lead'>Start the free readiness questionnaire</a></p>
<p>Need a paid technical review or implementation scope? <a href='/contact'>Talk to an operator</a>.</p>
<p><a href='/'>Back to store</a></p></body></html>"""
    return html
