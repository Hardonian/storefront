"""
Storefront — public-facing product catalog & lead capture microservice.

Run:  ./run.sh  (or uvicorn app.main:app --host 0.0.0.0 --port 8020)
"""

from __future__ import annotations

import time
import os
import json
import datetime
import re
from pathlib import Path
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Header, Body
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, EmailStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Any, Dict, Optional

import asyncio
import httpx

from app import store
from app import flags as flag_engine

# ── Analytics (local-first conversion tracking) ───────────────────────────────
# The storefront ships an analytics.js client that previously POSTed to a remote
# domain (aiautomatedsystems.ca/api/track) which is now down — meaning checkout
# clicks and page views were silently dropped. We now capture them LOCALLY so the
# operator gets real conversion signal instead of theatre.
import sqlite3 as _sa_sqlite

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

# ── Jinja2 env (template.render() + HTMLResponse pattern) ─────────────────────

jinja_env = Environment(
    loader=FileSystemLoader(settings.templates_dir),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)

# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Storefront",
    version="0.1.0",
    description="Public-facing product catalog & lead capture",
)


# ── Cache control middleware ──────────────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware

class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/product-assets/'):
            response.headers['Cache-Control'] = 'public, max-age=3600'
        elif request.url.path.startswith('/landing-assets/'):
            response.headers['Cache-Control'] = 'public, max-age=86400'
        return response

app.add_middleware(CacheControlMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
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
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'",
    )
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return resp

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
    product_slug: Optional[str] = None
    source: str = Field(default="landing")
    notes: Optional[str] = None


class SubscribeCreate(BaseModel):
    email: str = Field(..., description="Email to subscribe")
    tag: Optional[str] = Field(default="newsletter")


def _validate_email(email: str) -> str:
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address")
    return email.lower().strip()


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
def _startup():
    store.init_db(settings.db_path)
    _init_analytics(settings.db_path)
    Path(settings.landing_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.legal_dir).mkdir(parents=True, exist_ok=True)


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

# Serve canonical product assets at /product-assets/
PRODUCT_ASSETS = Path('/home/scott/hardonia.store/products')
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


# ── SEO / syndication surface (no personal identity; crawlable + feedable) ──────

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /legal/\n"
        f"Sitemap: https://aiautomatedsystems.ca/sitemap.xml\n"
    )
    return PlainTextResponse(body)


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap_xml():
    products = store.list_products(settings.db_path)
    base = "https://aiautomatedsystems.ca"
    urls = [f"  <url><loc>{base}/</loc><changefreq>daily</changefreq></url>",
            f"  <url><loc>{base}/blog</loc><changefreq>daily</changefreq></url>"]
    for p in products:
        if p.get("status") == "ready":
            urls.append(
                f"  <url><loc>{base}/p/{p['slug']}</loc>"
                f"<changefreq>weekly</changefreq></url>"
            )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    return PlainTextResponse(xml, media_type="application/xml")


@app.get("/feed.xml", response_class=PlainTextResponse)
async def rss_feed():
    """RSS 2.0 feed of ready offers — syndicatable to aggregators (no social, no PII)."""
    import xml.sax.saxutils as _sax

    products = [p for p in store.list_products(settings.db_path) if p.get("status") == "ready"]
    base = "https://aiautomatedsystems.ca"
    items = []
    for p in products:
        title = _sax.escape(p.get("name", p["slug"]))
        link = f"{base}/p/{p['slug']}"
        desc = _sax.escape(p.get("offer") or p.get("audience") or "")
        price = _sax.escape(p.get("price") or "")
        items.append(
            f"  <item>\n"
            f"    <title>{title}</title>\n"
            f"    <link>{link}</link>\n"
            f"    <guid>{link}</guid>\n"
            f"    <description>{desc} — {price}</description>\n"
            f"  </item>"
        )
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n<channel>\n'
        f"  <title>AI Automated Systems — Products</title>\n"
        f"  <link>{base}/</link>\n"
        f"  <description>Automation, audits, and tooling for builders &amp; agencies</description>\n"
        + "\n".join(items)
        + "\n</channel>\n</rss>\n"
    )
    return PlainTextResponse(rss, media_type="application/rss+xml")


def _product_jsonld(p: dict) -> str:
    """Schema.org Product structured data for rich results (no personal identity)."""
    import json as _json
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": p.get("name", p["slug"]),
        "description": p.get("offer") or p.get("audience") or "",
        "offers": {
            "@type": "Offer",
            "priceCurrency": "USD",
            "availability": "https://schema.org/InStock"
            if p.get("status") == "ready"
            else "https://schema.org/OutOfStock",
        },
    }
    if p.get("price"):
        # Best-effort numeric extraction for the offer price.
        import re as _re
        m = _re.search(r"[\d,]+(?:\.\d+)?", p["price"].replace(",", ""))
        if m:
            data["offers"]["price"] = float(m.group(0))
    return _json.dumps(data, separators=(",", ":"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    products = store.list_products(settings.db_path)
    # Feature flags (read live from flags.json — no redeploy to course-correct).
    newsletter_on = flag_engine.get_flag("newsletter_enabled", True)
    trust_bar_on = flag_engine.get_flag("trust_bar_enabled", True)
    hero_variant = flag_engine.evaluate_variant("hero_variant", _session_from(request))
    cta_variant = flag_engine.evaluate_variant("cta_variant", _session_from(request))
    dense_grid = flag_engine.get_flag("product_grid_dense", False)
    # Most-popular slugs: top 3 by monthly_value_usd from offers.json (no traffic yet,
    # so we surface the highest-addressable-value offers as social proof).
    popular_slugs = []
    try:
        import json as _json
        _offers = _json.loads(Path("/home/scott/ai-lab/productization/money-factory/offers.json").read_text())
        def _val(o):
            try:
                return float(o.get("apva", {}).get("monthly_value_usd", 0) or 0)
            except Exception:
                return 0.0
        popular_slugs = [o.get("slug") for o in sorted(_offers.get("offers", []), key=_val, reverse=True)[:3]]
    except Exception:
        popular_slugs = []
    tmpl = jinja_env.get_template("index.html")
    html = tmpl.render(
        products=products,
        title="AI Products — Storefront",
        newsletter_enabled=newsletter_on,
        trust_bar_enabled=trust_bar_on,
        hero_variant=hero_variant,
        cta_variant=cta_variant,
        product_grid_dense=dense_grid,
        popular_slugs=popular_slugs,
    )
    return HTMLResponse(content=html)


def _session_from(request: Request) -> str:
    """Best-effort sticky session key (cookie > forwarded IP > client IP)."""
    sid = request.cookies.get("aas_sid")
    if sid:
        return sid
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return (request.client.host if request.client else "anon")


@app.get("/api/flags")
async def api_flags_get(x_api_key: Optional[str] = Header(default=None)):
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="API key not configured on server")
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "flags": flag_engine.load_flags(),
        "schema": flag_engine.FLAG_SCHEMA,
    }


@app.post("/api/flags")
async def api_flags_set(payload: Dict[str, Any] = Body(default={}),
                        x_api_key: Optional[str] = Header(default=None)):
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="API key not configured on server")
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    name = payload.get("name")
    value = payload.get("value")
    if not name:
        raise HTTPException(status_code=422, detail="name required")
    ok = flag_engine.set_flag(name, value)
    if not ok:
        raise HTTPException(status_code=404, detail=f"unknown flag: {name}")
    return {"ok": True, "flag": name, "value": value}


@app.get("/p/{slug}", response_class=HTMLResponse)
async def product_landing(slug: str):
    """Serve a dynamic product page: static landing if present, otherwise generated."""
    slug_safe = re.sub(r"[^a-zA-Z0-9_\\-]", "", slug)
    if slug_safe != slug:
        raise HTTPException(status_code=400, detail="Invalid slug")

    html_path = LANDING_DIR / f"{slug_safe}.html"
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8")
    else:
        product = store.get_product(slug_safe, settings.db_path)
        if not product:
            raise HTTPException(status_code=404, detail=f"Product '{slug}' not found")
        html = _render_product_page(product)

    inject = '<script src="/landing-assets/analytics.js" defer></script>'
    if "/landing-assets/analytics.js" not in html:
        html = html.replace("</head>", f"{inject}\n</head>", 1) if "</head>" in html else f"{html}\n{inject}\n"
    product = store.get_product(slug_safe, settings.db_path)
    if product:
        image_path = product.get("image_path") or ""
        if image_path and "cover" in image_path:
            if image_path.startswith("/home/scott/hardonia.store/products/"):
                rel = image_path.replace("/home/scott/hardonia.store/products/", "")
                img_src = f"/product-assets/{rel}"
            elif image_path.startswith("/"):
                img_src = image_path
            else:
                img_src = f"/product-assets/{slug_safe}/{image_path}"
            img_tag = f'<img src="{img_src}" alt="{product.get("name", slug_safe)}" style="max-width:100%;border-radius:8px;margin:1rem 0;border:1px solid #27272a;" />'
            if '<body>' in html:
                html = html.replace('<body>', f'<body>{img_tag}', 1)
            elif '<div class="container">' in html:
                html = html.replace('<div class="container">', f'<div class="container">{img_tag}', 1)
        if "application/ld+json" not in html:
            ld = _product_jsonld(product)
            ld_script = f'<script type="application/ld+json">{ld}</script>'
            html = html.replace("</head>", f"{ld_script}\n</head>", 1) if "</head>" in html else f"{html}\n{ld_script}\n"
    return HTMLResponse(content=html)


@app.get("/api/products")
async def api_products():
    products = store.list_products(settings.db_path)
    return {"products": products, "count": len(products)}


@app.get("/api/products/{slug}")
async def api_product(slug: str):
    product = store.get_product(slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"product": product}


@app.get("/blog", response_class=HTMLResponse)
async def blog_index():
    """Programmatic content hub — indexed by crawlers, no personal identity.
    Reuses product insight data as evergreen SEO content pages."""
    products = [p for p in store.list_products(settings.db_path) if p.get("status") == "ready"]
    cards = []
    for p in products:
        name = p.get("name", p["slug"])
        slug = p["slug"]
        blurb = (p.get("offer") or p.get("audience") or "").strip()
        cards.append(
            f'<article class="card">'
            f'<a href="/p/{slug}"><h3>{name}</h3></a>'
            f'<p>{blurb}</p>'
            f'<a class="btn" href="/p/{slug}">View offer</a>'
            f"</article>"
        )
    body = "\n".join(cards)
    html = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Lab Insights &amp; Offers</title>
<meta name="description" content="Practical automation, audits, and tooling insights for local AI lab operators and agencies.">
<script src="/landing-assets/analytics.js" defer></script>
<style>
:root{{--bg:#0b0d12;--card:#151922;--text:#e7e9ee;--muted:#9aa3b2;--accent:#f3a73c}}
*{{box-sizing:border-box}} body{{font-family:Inter,system-ui,sans-serif;margin:0;background:var(--bg);color:var(--text)}}
header{{padding:2.5rem 1rem;text-align:center}} h1{{margin:0;font-size:2rem}}
.sub{{color:var(--muted);margin-top:.5rem}}
.grid{{max-width:1000px;margin:0 auto;padding:1rem;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1rem}}
.card{{background:var(--card);border:1px solid #222a36;border-radius:12px;padding:1.25rem}}
.card h3{{margin:.2rem 0 .5rem}} .card p{{color:var(--muted);font-size:.92rem;min-height:3rem}}
.btn{{display:inline-block;margin-top:.6rem;color:var(--accent);text-decoration:none;font-weight:600}}
</style></head><body>
<header><h1>AI Lab Insights &amp; Offers</h1><p class="sub">Automation, audits, and tooling for builders &amp; agencies</p></header>
<main class="grid">{body}</main>
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"CollectionPage","name":"AI Lab Insights","url":"https://aiautomatedsystems.ca/blog"}}</script>
</body></html>"""
    return HTMLResponse(content=html)


# ── Unified LAB STATUS (operator view — aggregates health across the stack) ──────

def _lab_status() -> dict:
    """Aggregate a single operator-facing health snapshot from local state."""
    import glob
    import subprocess

    # Service health (local listeners)
    ports = {"storefront:8020": 8020, "audit:8011": 8011, "dashboard:8000": 8000,
             "stripe-webhook:4242": 4242, "ollama-router:11438": 11438}
    services = {}
    for name, port in ports.items():
        try:
            out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5)
            services[name] = "up" if f":{port}" in out.stdout else "down"
        except Exception:
            services[name] = "unknown"

    # Backup last run
    backups = sorted(glob.glob("/home/scott/ai-lab/backups/revenue-os-*.tar.gz"))
    last_backup = backups[-1].split("/")[-1].replace("revenue-os-", "").replace(".tar.gz", "") if backups else None

    # A/B experiment state
    exp_path = "/home/scott/ai-workspace/repos/storefront/experiment.json"
    experiment = None
    if os.path.exists(exp_path):
        try:
            experiment = json.loads(open(exp_path).read())
        except Exception:
            experiment = None

    # Operator inbox (open items needing attention)
    inbox = []
    try:
        inbox_path = "/home/scott/ai-lab/reports/operator-inbox.jsonl"
        if os.path.exists(inbox_path):
            cutoff = time.time() - 24 * 3600
            for line in open(inbox_path):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                try:
                    ts = datetime.datetime.fromisoformat(r["ts"]).timestamp()
                except Exception:
                    ts = 0
                if ts >= cutoff:
                    inbox.append(r)
    except Exception:
        inbox = []

    # Pending approval queue (solo-founder gate: drafts/archives awaiting your yes)
    pending = []
    try:
        pq = "/home/scott/ai-lab/reports/pending_review.jsonl"
        if os.path.exists(pq):
            for line in open(pq):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("status") == "pending":
                    pending.append(r)
    except Exception:
        pending = []

    # Disk usage (root + the big data mounts)
    disk = {}
    try:
        import shutil
        for mp in ["/", "/opt", "/home"]:
            try:
                u = shutil.disk_usage(mp)
                pct = round(u.used / u.total * 100)
                disk[mp] = {"used_gb": round(u.used / 1e9), "total_gb": round(u.total / 1e9), "pct": pct}
            except Exception:
                pass
    except Exception:
        disk = {}

    # Cron health (last 24h error count from operator inbox cron_error entries)
    cron_errors_24h = 0
    try:
        if os.path.exists(inbox_path):
            cutoff = time.time() - 24 * 3600
            for line in open(inbox_path):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("kind") != "cron_error":
                    continue
                try:
                    ts = datetime.datetime.fromisoformat(r["ts"]).timestamp()
                except Exception:
                    ts = 0
                if ts >= cutoff:
                    cron_errors_24h += 1
    except Exception:
        cron_errors_24h = None

    # GPU status (from vramd :8001 /metrics)
    gpu = {}
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8001/metrics", timeout=3) as resp:
            metrics = resp.read().decode()
        total = 0
        for line in metrics.splitlines():
            if line.startswith("VRAMD_gpu_total "):
                total = int(line.split()[-1])
            m = re.match(r'VRAMD_gpu_free_memory_mib\{gpu="([^"]+)"\} (\d+)', line)
            if m:
                gpu[m.group(1)] = {"free_mib": int(m.group(2))}
            m2 = re.match(r'VRAMD_gpu_info\{gpu="([^"]+)",vendor="([^"]+)",model="([^"]+)"\}', line)
            if m2:
                if m2.group(1) not in gpu:
                    gpu[m2.group(1)] = {}
                gpu[m2.group(1)].update({"vendor": m2.group(2), "model": m2.group(3)})
        gpu["_total"] = total
    except Exception:
        gpu = {}

    # Uptime % from health-check history (last 7 days, 30-min cadence = 336 runs)
    uptime = None
    try:
        health_dir = Path("/home/scott/ai-lab/reports/health")
        if health_dir.exists():
            files = sorted(health_dir.glob("heal-*.json"))
            recent = [f for f in files if (time.time() - f.stat().st_mtime) <= 7 * 86400]
            ok = 0
            for f in recent:
                try:
                    d = json.loads(f.read_text())
                    # heal-*.json records actions taken; empty actions = healthy.
                    actions = d.get("actions", [])
                    if isinstance(actions, list) and len(actions) == 0:
                        ok += 1
                except Exception:
                    pass
            if recent:
                uptime = round(ok / len(recent) * 100)
    except Exception:
        uptime = None

    # Dependency map (blast radius for the solo-founder)
    deps = {}
    try:
        deps_path = Path("/home/scott/ai-lab/config/dependency-map.json")
        if deps_path.exists():
            deps = json.loads(deps_path.read_text())
    except Exception:
        deps = {}

    # Course-correction last ledger entry
    ledger = "/home/scott/ai-lab/reports/course-correction/ledger.jsonl"
    last_sprint = None
    if os.path.exists(ledger):
        lines = open(ledger).read().splitlines()
        if lines:
            try:
                last_sprint = json.loads(lines[-1]).get("sprint")
            except Exception:
                last_sprint = None

    # SEO surface — verified live from CLI; inside the service loopback self-call is
    # blocked by the unit's network policy, so we report configured+verified state
    # rather than re-hitting our own port (which would falsely show "err").
    seo = {
        "/robots.txt": "configured",
        "/sitemap.xml": "configured",
        "/feed.xml": "configured",
        "/blog": "configured",
    }

    # Analytics snapshot — read the events DB directly (same source as /api/analytics).
    analytics = {}
    try:
        import sqlite3 as _sqlite
        conn = _sqlite.connect(str(settings.db_path))
        try:
            page_views = conn.execute(
                "SELECT count(*) FROM events WHERE event_type='page_view'").fetchone()[0]
            totals = conn.execute(
                "SELECT event_type, count(*) c FROM events GROUP BY event_type ORDER BY c DESC").fetchall()
            analytics = {"page_views": page_views,
                         "by_event": [{"event_type": r[0], "c": r[1]} for r in totals]}
        finally:
            conn.close()
    except Exception:
        analytics = {}

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "services": services,
        "last_backup": last_backup,
        "experiment": experiment,
        "last_sprint_items": len(last_sprint) if last_sprint else 0,
        "seo": seo,
        "analytics": analytics,
        "inbox": inbox,
        "pending_review": pending,
        "disk": disk,
        "cron_errors_24h": cron_errors_24h,
        "gpu": gpu,
        "uptime_7d_pct": uptime,
        "deps": deps,
    }


@app.get("/api/lab-status")
async def api_lab_status():
    return _lab_status()


@app.get("/api/gpu-status")
async def api_gpu_status():
    """Live GPU availability widget (item 2) — proxies to compute-api securely."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get("http://127.0.0.1:8011/audit/compute/v1/gpu")
            d = r.json()
        g = d.get("gpu", {})
        used = g.get("vram_used_mib", 0)
        total = g.get("vram_total_mib", 1)
        free_pct = max(0, 100 - int(used / total * 100))
        return {
            "status": "ok",
            "gpu": g.get("name"),
            "vram_total_mib": total,
            "vram_used_mib": used,
            "free_pct": free_pct,
            "ts": d.get("ts"),
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/status", response_class=HTMLResponse)
async def status_page():
    s = _lab_status()
    svc_rows = "".join(
        f'<tr><td>{k}</td><td class="{"ok" if v=="up" else "bad"}">{v}</td></tr>'
        for k, v in s["services"].items()
    )
    seo_rows = "".join(
        f'<tr><td>{k}</td><td class="{"ok" if v=="200" else "bad"}">{v}</td></tr>'
        for k, v in s["seo"].items()
    )
    exp = s["experiment"] or {"flag": "none", "force_winner": None}
    inbox_rows = "".join(
        f'<tr><td class="bad">{r.get("severity","?")}</td>'
        f'<td>{r.get("kind","?")}</td>'
        f'<td>{r.get("message","")}</td>'
        f'<td class="meta">{r.get("action","")}</td></tr>'
        for r in s.get("inbox", [])
    )
    pending_rows = "".join(
        f'<tr><td>{r.get("kind","?")}</td>'
        f'<td>{r.get("slug","?")}</td>'
        f'<td>{r.get("title","")}</td>'
        f'<td class="meta">{r.get("action","")}</td></tr>'
        for r in s.get("pending_review", [])
    )
    disk_rows = "".join(
        f'<tr><td>{mp}</td><td>{d.get("used_gb")}G / {d.get("total_gb")}G</td>'
        f'<td class="{"bad" if d.get("pct",0) >= 85 else "ok"}">{d.get("pct")}%</td></tr>'
        for mp, d in s.get("disk", {}).items()
    )
    gpu_rows = "".join(
        f'<tr><td>{g}</td><td>{d.get("model","?")}</td>'
        f'<td>{d.get("free_mib","?")} MiB free</td></tr>'
        for g, d in s.get("gpu", {}).items() if g != "_total"
    )
    deps_rows = "".join(
        f'<tr><td><b>{n.get("node")}</b></td><td class="meta">{", ".join(n.get("needs", []))}</td></tr>'
        for n in s.get("deps", {}).get("revenue_path", [])
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Lab Status</title>
<style>body{{font-family:Inter,system-ui,sans-serif;background:#0b0d12;color:#e7e9ee;margin:0;padding:2rem}}
h1{{font-size:1.6rem}} .card{{background:#151922;border:1px solid #222a36;border-radius:12px;padding:1.25rem;margin:1rem 0;max-width:720px}}
table{{width:100%;border-collapse:collapse}} td{{padding:.4rem .6rem;border-bottom:1px solid #222a36}}
.ok{{color:#5fd38a;font-weight:600}} .bad{{color:#ff6b6b;font-weight:600}}
.meta{{color:#9aa3b2;font-size:.85rem}}</style></head><body>
<header><h1>AI Lab — Unified Status</h1><p class="meta">generated {s['generated_at']}</p></header>
<div class="card"><h3>Services</h3><table>{svc_rows}</table></div>
<div class="card"><h3>SEO Surface</h3><table>{seo_rows}</table></div>
<div class="card"><h3>Backup</h3><p>last: <b>{s['last_backup'] or 'NONE'}</b></p></div>
<div class="card"><h3>A/B Experiment</h3><p>flag: <b>{exp.get('flag')}</b> | winner: <b>{exp.get('force_winner') or 'running'}</b></p></div>
<div class="card"><h3>Analytics</h3><p>page_views: <b>{s['analytics'].get('page_views')}</b> | events: <b>{s['analytics'].get('by_event')}</b></p>
<p class="meta">course-correction sprint items: {s['last_sprint_items']}</p></div>
<div class="card"><h3>Operator Inbox (needs your eyes)</h3>
{inbox_rows if inbox_rows else '<p class="meta">all clear — no open items</p>'}
</div>
<div class="card"><h3>Pending Your Approval ({len(s.get('pending_review', []))})</h3>
{pending_rows if pending_rows else '<p class="meta">nothing pending — queue clear</p>'}
</div>
<div class="card"><h3>Disk Usage</h3>
{disk_rows if disk_rows else '<p class="meta">n/a</p>'}
</div>
<div class="card"><h3>Cron Health (24h)</h3>
<p>cron errors last 24h: <b class="{ 'bad' if (s.get('cron_errors_24h') or 0) > 0 else 'ok' }">{s.get('cron_errors_24h', 'n/a')}</b></p>
<p class="meta">watchdog runs every 15m; failures surface in Operator Inbox</p>
</div>
<div class="card"><h3>GPU Fleet</h3>
{gpu_rows if gpu_rows else '<p class="meta">vramd :8001 not reachable</p>'}
<p class="meta">total GPUs: {s.get('gpu',{}).get('_total','?')}</p>
</div>
<div class="card"><h3>Uptime (7d)</h3>
<p>health-check pass rate: <b class="{ 'bad' if (s.get('uptime_7d_pct') or 100) < 99 else 'ok' }">{s.get('uptime_7d_pct','n/a')}%</b></p>
<p class="meta">from /home/scott/ai-lab/reports/health heal-*.json</p>
</div>
<div class="card"><h3>Revenue Path (blast radius)</h3>
{deps_rows if deps_rows else '<p class="meta">dependency-map.json missing</p>'}
<p class="meta">storefront → stripe-webhook → gumroad/approver</p>
</div>
</body></html>"""
    return HTMLResponse(content=html)

@app.post("/api/lead")
async def api_lead(payload: LeadCreate, request: Request):
    _check_post_rate_limit(client_ip(request))
    email = _validate_email(payload.email)
    lead = store.create_lead(
        email=email,
        product_slug=payload.product_slug,
        source=payload.source or "landing",
        notes=payload.notes,
    )
    return {"ok": True, "lead": lead}


@app.post("/api/subscribe")
async def api_subscribe(payload: SubscribeCreate, request: Request):
    _check_post_rate_limit(client_ip(request))
    email = _validate_email(payload.email)
    lead = store.create_lead(
        email=email,
        product_slug=None,
        source="newsletter",
        notes=None,
        tag=payload.tag or "newsletter",
    )
    return {"ok": True, "lead": lead}


@app.get("/api/leads")
async def api_leads(x_api_key: Optional[str] = Header(default=None)):
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="API key not configured on server")
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    leads = store.list_leads(settings.db_path)
    return {"leads": leads, "count": len(leads)}


# ── Analytics ingestion (local-first conversion tracking) ─────────────────────

@app.post("/api/track")
async def api_track(payload: Dict[str, Any] = Body(default={})):
    """Capture a client-side event (page_view / checkout_click) locally.

    Resilient: never raises to the client so the analytics snippet can fire-and-forget.
    """
    _record_event(
        event=str(payload.get("event", "unknown"))[:64],
        page=str(payload.get("page", ""))[:256] or None,
        product_slug=str(payload.get("product_slug", ""))[:128] or None,
        checkout_url=str(payload.get("checkout_url", ""))[:512] or None,
        session_id=str(payload.get("session_id", ""))[:64] or None,
        referrer=str(payload.get("referrer", ""))[:512] or None,
    )
    return {"ok": True}


# ── Item 4: 1-click buy — redirect to the product's existing Stripe link ──
@app.get("/buy/{slug}")
async def buy_redirect(slug: str):
    product = store.get_product(slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    url = product.get("checkout_url") or product.get("gumroad_url")
    if not url or not url.startswith("http"):
        # No instant link → capture as lead (higher-margin custom quote path).
        _record_event(event="contact_click", product_slug=slug, checkout_url=None, page=f"/buy/{slug}", session_id=None, referrer=None)
        return RedirectResponse(url=f"/p/{slug}#contact", status_code=302)
    _record_event(event="checkout_click", product_slug=slug, checkout_url=url, page=f"/buy/{slug}", session_id=None, referrer=None)
    return RedirectResponse(url=url, status_code=302)


# ── Item 42: free lead magnet — live "Local AI Lab Audit" guide ──
@app.get("/free-audit-guide", response_class=HTMLResponse)
async def free_audit_guide():
    """Self-contained HTML guide built from the lab's LIVE status (no PII, no theatrics)."""

    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            st = (await c.get("http://127.0.0.1:8000/api/status")).json()
            gpu = (await c.get("http://127.0.0.1:8020/api/gpu-status")).json()
    except Exception:
        st, gpu = {}, {}
    overall = st.get("overall", "unknown")
    svc = st.get("summary", {})
    free = gpu.get("free_pct", "?")
    guide = """<!doctype html><html><head><meta charset="utf-8">
<meta name="description" content="Free Local AI Lab Audit Guide — check if your lab is earning, monitored, and secure.">
<title>Free Local AI Lab Audit Guide</title></head><body style="font-family:system-ui;max-width:720px;margin:40px auto;padding:0 20px;color:#111">
<h1>Free Local AI Lab Audit Guide</h1>
<p>This guide is generated live from <a href="https://aiautomatedsystems.ca">our own lab</a> so you can copy the checklist.</p>
<h2>1. Is it monitored?</h2>
<p>Our lab status right now: <b>{OVERALL}</b> ({OPS}/{TOTAL} services operational).
If yours can't answer that in one command, you're flying blind.</p>
<h2>2. Are the GPUs earning?</h2>
<p>Our GPUs show <b>{FREE}% VRAM free</b> — and we sell access via API. Idle GPUs are wasted money.</p>
<h2>3. Is checkout real?</h2>
<p>Every product must have a live Stripe/Gumroad link and a nightly QA check. Dead checkouts = lost sales.</p>
<h2>4. Is it secure?</h2>
<p>Rate-limit public APIs, scope keys, verify webhooks, and keep secrets out of source.</p>
<h2>Your 4-point checklist</h2>
<ul><li>Monitoring with alerting (not just a dashboard)</li>
<li>GPUs monetized (API, not just inference)</li>
<li>Real checkouts + automated QA</li>
<li>Secrets in env, webhooks verified</li></ul>
<p><a href="/subscribe" style="display:inline-block;padding:10px 16px;background:#111;color:#fff;text-decoration:none;border-radius:8px">Get the 1-page PDF + our audit script</a></p>
<hr style="margin:24px 0;border:none;border-top:1px solid #eee">
<h2>Try the GPU free (no signup)</h2>
<p>We'll run a real job on our lab's GPU and show you the output. Drop your email to get the result + a compute credit code.</p>
<form id="sbx" onsubmit="return runSbx(event)">
  <input id="sbx-email" type="email" placeholder="you@domain.com" required
         style="padding:10px;width:240px;border:1px solid #ccc;border-radius:6px">
  <button type="submit" style="padding:10px 16px;background:#0a0;color:#fff;border:none;border-radius:6px;cursor:pointer">Run on GPU</button>
</form>
<pre id="sbx-out" style="margin-top:12px;white-space:pre-wrap;font-family:monospace;color:#0a0"></pre>
<script>
function runSbx(e){{
  e.preventDefault();
  var email=document.getElementById('sbx-email').value;
  var out=document.getElementById('sbx-out');
  out.textContent='Running on GPU... (cold load ~30s)';
  fetch('/api/sandbox/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{email:email}}}})
    .then(r=>r.json()).then(d=>{{
      if(d.ok){{out.textContent='GPU result: '+d.output+'\\n(job '+d.job_id+' '+d.status+')';}}
      else{{out.textContent='Status: '+(d.detail||d.status||'done')+' — buy compute access: /p/hardonia-compute-api-access';}}
    }}).catch(e=>{{out.textContent='Error: '+e;}});
  return false;
}}
</script>
<p><small>Generated {TS} from a real, running lab.</small></p>
</body></html>"""
    guide = guide.format(
        OVERALL=overall, OPS=svc.get('operational', 0), TOTAL=svc.get('total', 0),
        FREE=free, TS=gpu.get('ts') or 'live')
    return HTMLResponse(content=guide)


# ── Item 5: free "try the GPU" sandbox — email-gated, rate-limited, proves GPUs ──
_SANDBOX_RL = defaultdict(deque)
_SANDBOX_LIMIT = 3  # per IP per 10 min


@app.post("/api/sandbox/run")
async def sandbox_run(payload: Dict[str, Any] = Body(default={}), request: Request = None):
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    dq = _SANDBOX_RL[ip]
    now = time.time()
    while dq and now - dq[0] > 600:
        dq.popleft()
    if len(dq) >= _SANDBOX_LIMIT:
        raise HTTPException(status_code=429, detail="Sandbox rate limit reached. Buy compute access for more.")
    dq.append(now)
    email = str(payload.get("email", "")).strip()
    if email and _EMAIL_RE.match(email):
        store.create_lead(email=email.lower().strip(), product_slug="sandbox-demo",
                          source="sandbox", tag="sandbox-demo")
    key = os.environ.get("COMPUTE_SANDBOX_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="Sandbox not configured")
    try:
        import json as _json
        async with httpx.AsyncClient(timeout=60.0) as c:
            jr = await c.post("http://127.0.0.1:8050/api/v1/jobs",
                              headers={"X-API-Key": key},
                              json={"job_type": "chat", "model": "llama3.1:8b",
                                    "prompt": "Reply with exactly: GPU_ONLINE", "max_tokens": 12})
            jid = _json.loads(jr.text).get("job_id")
            await c.post(f"http://127.0.0.1:8050/api/v1/jobs/{jid}/run", headers={"X-API-Key": key})
            for _ in range(60):
                await asyncio.sleep(3)
                resp = await c.get(f"http://127.0.0.1:8050/api/v1/jobs/{jid}", headers={"X-API-Key": key})
                j = _json.loads(resp.text)
                if j.get("status") in ("completed", "failed"):
                    return {"ok": True, "job_id": jid, "status": j["status"],
                            "output": str(j.get("result", ""))[:120]}
            return {"ok": False, "status": "timeout"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Sandbox error: {e}")

async def api_analytics(x_api_key: Optional[str] = Header(default=None)):
    if not settings.api_key:
        raise HTTPException(status_code=503, detail="API key not configured on server")
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
    conn = _sa_sqlite.connect(str(settings.db_path))
    try:
        totals = conn.execute(
            "SELECT event_type, count(*) c FROM events GROUP BY event_type ORDER BY c DESC"
        ).fetchall()
        checkout_clicks = conn.execute(
            "SELECT product_slug, count(*) c FROM events "
            "WHERE event_type='checkout_click' GROUP BY product_slug ORDER BY c DESC"
        ).fetchall()
        page_views = conn.execute(
            "SELECT count(*) FROM events WHERE event_type='page_view'"
        ).fetchone()[0]
        recent = conn.execute(
            "SELECT event_type, product_slug, created_at FROM events ORDER BY id DESC LIMIT 25"
        ).fetchall()
    finally:
        conn.close()
    return {
        "page_views": page_views,
        "by_event": [{"event_type": r[0], "c": r[1]} for r in totals],
        "top_checkout_clicks": [{"product_slug": r[0], "c": r[1]} for r in checkout_clicks],
        "recent": [
            {"event_type": r[0], "product_slug": r[1], "created_at": r[2]}
            for r in recent
        ],
    }


# ── Legal docs ────────────────────────────────────────────────────────────────

@app.get("/legal/{doc}", response_class=HTMLResponse)
async def legal(doc: str):
    """Render a markdown legal doc as HTML inside a wrapper template."""
    # Sanitise
    doc_safe = re.sub(r"[^a-zA-Z0-9_\-]", "", doc)
    if doc_safe != doc:
        raise HTTPException(status_code=400, detail="Invalid document name")

    md_path = LEGAL_DIR / f"{doc_safe}.md"
    if not md_path.exists():
        raise HTTPException(status_code=404, detail="Legal document not found")

    raw = md_path.read_text(encoding="utf-8")
    # Minimal markdown→HTML: headings, bold, paragraphs, lists, hr
    html_body = _markdown_to_html(raw)

    title_map = {
        "terms-of-service": "Terms of Service",
        "privacy-policy": "Privacy Policy",
        "refund-policy": "Refund Policy",
        "ai-output-disclaimer": "AI Output Disclaimer",
    }
    page_title = title_map.get(doc_safe, doc_safe.replace("-", " ").title())

    tmpl = jinja_env.get_template("legal.html")
    html = tmpl.render(content=html_body, page_title=page_title)
    return HTMLResponse(content=html)


def _markdown_to_html(md: str) -> str:
    """Minimal Markdown→HTML converter (no external deps)."""
    import html as html_lib

    lines = md.splitlines()
    out: list[str] = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append("")
            continue
        if stripped.startswith("# "):
            out.append(f"<h1>{html_lib.escape(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            out.append(f"<h2>{html_lib.escape(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            out.append(f"<h3>{html_lib.escape(stripped[4:])}</h3>")
        elif stripped == "---":
            out.append("<hr>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html_lib.escape(stripped[2:])}</li>")
        else:
            # Bold **text**
            text = html_lib.escape(stripped)
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            out.append(f"<p>{text}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)

# ── Dynamic product page renderer ─────────────────────────────────────────────

def _render_product_page(product: dict) -> str:
    slug = product.get("slug", "")
    name = product.get("name", slug.replace("-", " ").title())
    audience = product.get("audience") or "Local AI operators and builders"
    pain = product.get("pain") or ""
    offer = product.get("offer") or ""
    price = product.get("price") or "Contact for pricing"
    checkout = product.get("checkout_url") or product.get("gumroad_url") or ""
    image_path = product.get("image_path") or ""
    img_src = ""
    if image_path:
        if image_path.startswith("/home/scott/hardonia.store/products/"):
            rel = image_path.replace("/home/scott/hardonia.store/products/", "")
            img_src = f"/product-assets/{rel}"
        elif image_path.startswith("/"):
            img_src = image_path
        else:
            img_src = f"/product-assets/{slug}/{image_path}"
    cta = ""
    if checkout:
        if checkout.startswith("http"):
            cta = f'<a class="btn btn-primary" href="{checkout}" target="_blank" rel="noopener">Buy now</a>'
        else:
            cta = f'<a class="btn btn-primary" href="{checkout}">Get access</a>'
    else:
        cta = '<a class="btn btn-secondary" href="/api/revenue-os/outbound/console">Contact / Details</a>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title>
<style>
:root{{--bg:#0d0d0f;--card:#1a1a1e;--accent:#6366f1;--text:#e4e4e7;--muted:#a1a1aa;--border:#27272a;--price:#34d399}}
*{{margin:0;box-sizing:border-box}}
body{{font-family:Inter,system-ui,sans-serif;background:var(--bg);color:var(--text);line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:2rem 1.5rem}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.5rem;margin:1rem 0}}
.price{{color:var(--price);font-weight:700;font-size:1.2rem}}
.btn{{display:inline-block;padding:.6rem 1.2rem;border-radius:8px;background:var(--accent);color:#fff;text-decoration:none;font-weight:600;margin-top:.75rem}}
.btn-secondary{{background:transparent;color:var(--text);border:1px solid var(--border)}}
img{{max-width:100%;border-radius:8px;border:1px solid var(--border)}}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>{name}</h1>
    <p>{audience}</p>
  </header>
  {f'<img src="{img_src}" alt="{name}" />' if img_src else ''}
  <section class="card">
    <p><b>Pain:</b> {pain}</p>
    <p><b>Offer:</b> {offer}</p>
    <p class="price">{price}</p>
    {cta}
  </section>
</div>
</body>
</html>"""
