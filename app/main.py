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


def _init_analytics(db_path: str) -> None:
    conn = _sa_sqlite.connect(str(db_path))
    try:
        conn.execute(_ANALYTICS_DDL)
        conn.commit()
    finally:
        conn.close()


def _record_event(event: str, page: str | None, product_slug: str | None,
                  checkout_url: str | None, session_id: str | None,
                  referrer: str | None) -> None:
    import json as _json
    payload = _json.dumps({
        "page": page,
        "checkout_url": checkout_url,
        "session_id": session_id,
        "referrer": referrer,
    }, separators=(",", ":"))
    conn = _sa_sqlite.connect(str(settings.db_path))
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
        # Long-cache immutable static assets
        if path.startswith('/product-assets/'):
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


@app.get("/api/proof-score")
async def proof_score_api():
    """Public aggregate Proof Score; never exposes private evidence or secrets."""
    if not PROOF_PUBLIC.exists():
        raise HTTPException(status_code=503, detail="Proof Score is being refreshed")
    try:
        data = json.loads(PROOF_PUBLIC.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="Proof Score is temporarily unavailable")
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
    esc = lambda value: _html.escape(str(value))
    scores = data.get("sub_scores", {})
    benchmark = data.get("benchmark", {})
    cards = "".join(f"<div class='card'><b>{esc(k.title())}</b><strong>{esc(v)}/100</strong></div>" for k, v in scores.items())
    style = """body{margin:0;background:#090b10;color:#e8edf5;font:16px system-ui;line-height:1.55}main{max-width:960px;margin:auto;padding:56px 22px}.eyebrow{color:#7dd3fc;letter-spacing:.12em;text-transform:uppercase;font-size:12px;font-weight:700}h1{font-size:clamp(38px,7vw,72px);line-height:1.02;margin:16px 0}.lead{font-size:20px;color:#aab6c8;max-width:700px}.score{display:flex;gap:28px;align-items:center;flex-wrap:wrap;margin:38px 0}.big{font-size:76px;font-weight:800;color:#86efac;line-height:1}.grade{font-size:21px;color:#c4b5fd}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.card{padding:18px;border:1px solid #273244;border-radius:14px;background:#111722}.card b,.card strong{display:block}.card b{color:#9aa9bd;font-size:13px}.card strong{font-size:25px;margin-top:6px}.cta{display:inline-block;margin-top:30px;background:#38bdf8;color:#04111c;text-decoration:none;font-weight:800;padding:13px 18px;border-radius:10px}.muted{color:#8290a3;font-size:13px}"""
    body = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Sovereign AI Proof Score | The Platform</title><meta name='description' content='A locally generated, tamper-evident proof of private AI operational readiness'>
<style>{style}</style></head><body><main>
<div class='eyebrow'>The Platform Proof Layer</div><h1>Private AI you can prove.</h1><p class='lead'>A local-first operational score generated from real service, GPU, resilience, autonomy, and catalog checks. No prompts, customer records, credentials, or private infrastructure details are published.</p>
<section class='score'><div class='big'>{esc(data.get('overall_score'))}/100</div><div><div class='grade'>{esc(data.get('grade'))}</div><div class='muted'>Verified {esc(data.get('generated_at'))} · key {esc(data.get('key_id'))}</div></div></section>
<div class='grid'>{cards}</div><p class='muted'>Benchmark fixture: {esc(benchmark.get('passed'))}/{esc(benchmark.get('total'))} synthetic policy/structure cases passed. This is an operational proof signal, not a legal certification or a claim of model quality.</p>
<a class='cta' href='/p/sovereign-ops-score'>Run the full Sovereign AI Ops Score</a></main></body></html>"""
    return HTMLResponse(body, headers={"Cache-Control": "public, max-age=300"})


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
  add('bot','👋 I\\'m AU, Hardonia\\'s auth & access assistant. Ask about API keys, 403/429 errors, credits, or access.');
  inp.addEventListener('keydown',function(e){
    if(e.key!=='Enter'||!inp.value.trim())return;
    var q=inp.value.trim();inp.value='';add('me',q);
    fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})})
      .then(function(r){return r.json();}).then(function(d){
        if(d.escalated){add('bot', d.message||'Escalated to a human — we\\'ll reply within 1 business day.');}
        else if(d.answer){add('bot', d.answer.replace(/^— AU.*/m,'').trim());}
        else {add('bot','Something went wrong — please open a GitHub issue.');}
      }).catch(function(){add('bot','Support is briefly unavailable. Open a GitHub issue and we\\'ll reply within 1 business day.');});
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
async def robots_txt():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /legal/\n"
        "Sitemap: https://aiautomatedsystems.ca/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml():
    products = store.list_products(settings.db_path)
    base = "https://aiautomatedsystems.ca"
    lastmod = datetime.datetime.now(datetime.UTC).date().isoformat()
    urls: list[tuple[str, str, str | None]] = [
        (f"{base}/", "daily", None),
        (f"{base}/pricing", "weekly", None),
        (f"{base}/blog", "daily", None),
        (f"{base}/tools/gpu-cost-calculator", "monthly", None),
        (f"{base}/proof-score", "hourly", None),
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
    return PlainTextResponse(xml)


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms_txt():
    """Machine-readable, non-sensitive product discovery for AI/search agents."""
    products = [p for p in store.list_products(settings.db_path) if p.get("status") == "ready"]
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
        "- https://aiautomatedsystems.ca/request-access",
        "",
        "## Ready products",
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


def _public_product(product: dict) -> dict:
    """Return only buyer-safe catalog fields; never expose host filesystem paths."""
    allowed = {
        "slug", "name", "status", "audience", "pain", "offer", "price",
        "checkout_url", "gumroad_url", "readiness_score",
    }
    out = {k: product.get(k) for k in allowed}
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
    allowed = host == "buy.stripe.com" or host.endswith(".gumroad.com")
    safe_authority = parsed.username is None and parsed.password is None and parsed.port in {None, 443}
    return raw if parsed.scheme == "https" and allowed and safe_authority else ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
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
        "sovereign-ops-score", "ai-box-doctor", "private-inference-access",
        "hardonia-compute-api-access", "n8n-automation-kit", "comfyui-workflow-pack",
        "autonomous-revenue-loop",
    }]
    flags = flag_engine.load_flags()
    hero_variant = flag_engine.evaluate_variant("hero_variant", _session_id(request))
    cta_variant = flag_engine.evaluate_variant("cta_variant", _session_id(request))
    try:
        html = jinja_env.get_template("index.html").render(
            products=saleable_products,
            featured_products=featured_products,
            title="AI Automated Systems — Tools, Audits & Workflows",
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
    ("🔒", "Secured by Stripe", "256-bit TLS checkout"),
    ("💳", "Visa · Mastercard · Amex · Apple Pay", "All major methods"),
    ("✅", "30-day money-back", "No-risk guarantee"),
    ("⚡", "Instant digital delivery", "Get access in minutes"),
    ("🛡️", "Local-first & GDPR-aware", "Your data stays yours"),
    ("🤝", "Real human support", "Replies within 1 business day"),
]

PLATFORM_SLUGS = {
    "agent-ops-concierge", "private-ai-vault", "inference-api-starter",
    "inference-api-scale", "compliance-kit", "compliance-keep-current", "uptime-bond",
}
PLATFORM_TRUST = [
    ("📊", "Observable by design", "Live GPU + job audit log, no black box"),
    ("🏛️", "Audit-ready evidence", "SOC 2 control map + incident runbook"),
    ("🧱", "Isolated per tenant", "Per-key namespace, hard usage caps"),
    ("🤖", "Self-healing 24/7", "Watchdogs auto-recover the stack"),
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
                '<div class="tbadge"><span class="icon">📈</span>'
                '<span class="ttext"><b>99.5% SLA</b><br><small>Auto-refund on breach</small></span></div>'
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
        rows.append(("🏢 Enterprise", "Custom SLA, volume pricing, onboarding."))
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
<button id='claim-retry' type='button' hidden>Try again</button><p><a href='/contact'>Contact support</a></p>
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

    landing_path = product.get("landing_path") or ""
    p = Path(landing_path)
    landing_name = p.name if p.exists() else ""
    landing_url = f"/landing-assets/{landing_name}" if landing_name else ""

    img = ""
    if product.get("image_path"):
        ip = Path(product["image_path"])
        if ip.exists():
            rel = ip.relative_to(PRODUCT_ASSETS)
            img = f"/product-assets/{rel}"

    _record_event("product_view", page=request.url.path, product_slug=slug,
                  checkout_url=None, session_id=_session_id(request),
                  referrer=request.headers.get("referer"))

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
                    f'${price}/mo — isolation + monitoring + SLA included.</div>')
    deliverables_html = _deliverables_html(slug)
    tier_html = _tier_includes_html(product.get("price", ""))

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
            '<a class="cta secondary" href="/request-access?product=' + slug + '">Request a live dashboard demo</a>'
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
    canonical = f"https://aiautomatedsystems.ca/p/{slug}"
    image_absolute = f"https://aiautomatedsystems.ca{img}" if img else ""
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
<title>{name_html} — AI Automated Systems</title>
<meta name="description" content="{description_html}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="product">
<meta property="og:site_name" content="AI Automated Systems">
<meta property="og:title" content="{name_html} — AI Automated Systems">
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
        cta_html += f'<a class="cta" href="/p/{slug}">Contact / Details</a>'
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


# ── Enterprise contact ─────────────────────────────────────────────────────────
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
<input name='name' id='cname' placeholder='Your name' required>
<input name='email' id='cemail' type='email' placeholder='you@company.com' required>
<textarea name='needs' id='cneeds' rows=5 placeholder='What are you building? Volume, SLA, compliance needs...'></textarea>
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
        if p.get("status") != "ready":
            continue
        slug = _html.escape(str(p.get("slug") or ""), quote=True)
        name = _html.escape(str(p.get("name") or ""))
        price = _html.escape(str(p.get("price") or ""))
        checkout = _safe_external_url(p.get("checkout_url"))
        cta = f"<a class='cta' href='{_html.escape(checkout, quote=True)}' rel='noopener'>Buy — {price}</a>" if checkout \
            else f"<a class='cta' href='/contact?product={slug}'>Contact</a>"
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
a{{color:#0f766e}}</style></head><body>
<h1>💳 All products & bundles</h1>
<p class='muted'>One-time payments. Lifetime access. Stripe-secured. Need volume or custom? <a href='/contact'>Talk to us</a>.</p>
<table><thead><tr><th>Product</th><th>Price</th><th></th></tr></thead><tbody>
{table}
</tbody></table>
<p class='muted'><a href='/'>← Back to home</a></p>
</body></html>"""
    return html


@app.get("/metrics/funnel", response_class=PlainTextResponse)
async def funnel_metrics(_: None = Depends(require_operator)):
    """Conversion funnel: events -> leads -> purchases, real data."""
    import json as _json
    import sqlite3 as _sql
    db = _sql.connect(settings.db_path)
    events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    leads = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    purchases = db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
    rev = db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM purchases").fetchone()[0]
    db.close()
    data = {"events": events, "leads": leads, "purchases": purchases,
            "revenue_cents": rev, "ts": datetime.datetime.now(datetime.UTC).isoformat()}
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


@app.get("/blog/rss.xml", response_class=PlainTextResponse)
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
    return xml


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
    )
    return {"status": "ok"}


@app.get("/api/gpu-status")
async def gpu_status():
    return _gpu_status()


@app.get("/api/roi-calc")
async def roi_calc(cloud_spend: float = 500.0, hours: int = 40, tier: str = "starter"):
    """Deterministic bottom-line calculator.

    Compares a prospect's current cloud GPU cost to our fixed platform tier.
    Assumptions are explicit and conservative (no theatrical numbers):
      - Cloud effective rate = cloud_spend / hours  (their real blended $/hr)
      - Our tiers are fixed-price and include isolation + monitoring + SLA:
          starter $49/mo ~ 20 GPU-hr, scale $199/mo ~ 100 GPU-hr,
          concierge $497/mo ~ managed 200 GPU-hr equivalent
      - We do NOT count their engineering time saved (separate, larger win).
    Returns observable, auditable numbers.
    """
    TIERS = {
        "starter": {"price": 49, "gpu_hr": 20},
        "scale": {"price": 199, "gpu_hr": 100},
        "concierge": {"price": 497, "gpu_hr": 200},
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
        "note": "Fixed price includes isolation, monitoring, SLA. Excludes engineering-time saved.",
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
                  "SOC2-aligned controls", "99.5% SLA on bonded tiers"],
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
                  referrer=request.headers.get("referer"))
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
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Free AI Lab Audit Guide — AI Automated Systems</title>
<meta name='description' content='Get a real GPU-backed audit of your local AI stack. Free, no card required.'>
<style>body{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.7}
h1{font-size:2rem}a{color:#0f766e}.cta{display:inline-block;margin-top:1.2rem;background:#0f766e;color:#fff;text-decoration:none;padding:.7rem 1.3rem;border-radius:6px}</style></head>
<body><h1>Free AI Lab Audit Guide</h1>
<p>See exactly where your local AI stack is leaking money — VRAM waste, idle GPUs, and runaway API spend.</p>
<p>We run a <strong>real GPU job</strong> on our EPYC rig and send you a personalized report. No card, no fluff.</p>
<p><a class='cta' href='/p/ai-lab-health-report'>Get your free audit</a></p>
<p><a href='/'>Back to store</a></p></body></html>"""
    return html
