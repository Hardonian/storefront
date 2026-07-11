"""Storefront — public-facing product catalog & lead capture microservice.

Run:  ./run.sh  (or uvicorn app.main:app --host 0.0.0.0 --port 8020)
"""

from __future__ import annotations

import time
import os
import json
import datetime
import re
import subprocess
from pathlib import Path
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Header, Body, Query
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

from app.metrics import PrometheusMiddleware

app.add_middleware(PrometheusMiddleware, service_name="storefront")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://aiautomatedsystems.ca", "https://www.aiautomatedsystems.ca", "http://127.0.0.1:8020", "http://localhost:8020"],
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
    tag: Optional[str] = Field(default="lead")


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

# Serve standalone landing HTML previews at /landing/<slug>.html
# These are the generated, real-CTA product landing pages.
from fastapi.responses import FileResponse
import os

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


@app.get("/metrics")
async def metrics():
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
        + "\n".join(urls) + "\n"
        + "</urlset>\n"
    )
    return PlainTextResponse(xml)


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
                    total = g["vram_total_mib"]; used = g.get("vram_used_mib", 0)
                    free = round(100 * (total - used) / total)
                    return {"status": "ok", "free_pct": free, "gpu": g.get("name", "GPU")}
    except Exception:
        pass
    return {"status": "unknown", "free_pct": None, "gpu": "GPUs warming up"}


def _session_id(request: Request) -> str:
    return request.cookies.get("aas_sid") or "anon"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    products = store.list_products(settings.db_path)
    # Rewrite absolute image_path -> served /product-assets/ URL so covers load.
    for p in products:
        ip = p.get("image_path") or ""
        if ip.startswith("/home/scott/hardonia.store/products/"):
            p["image_path"] = "/product-assets/" + ip[len("/home/scott/hardonia.store/products/"):]
    flags = flag_engine.load_flags()
    hero_variant = flag_engine.evaluate_variant("hero_variant", _session_id(request))
    cta_variant = flag_engine.evaluate_variant("cta_variant", _session_id(request))
    try:
        html = jinja_env.get_template("index.html").render(
            products=products,
            title="AI Automated Systems — Tools, Audits & Workflows",
            hero_variant=hero_variant,
            cta_variant=cta_variant,
            newsletter_enabled=flags.get("newsletter_enabled", True),
            trust_bar_enabled=flags.get("trust_bar_enabled", True),
            product_grid_dense=flags.get("product_grid_dense", False),
            popular_slugs=POPULAR_SLUGS,
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

def _trust_row_html() -> str:
    badges = "".join(
        f'<div class="tbadge"><span class="ticon">{icon}</span>'
        f'<span class="ttext"><b>{title}</b><br><small>{sub}</small></span></div>'
        for icon, title, sub in TRUST_BADGES
    )
    return f'<div class="trust-row">{badges}</div>'

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
    trust_html = _trust_row_html()
    urgency_html = _urgency_badge()
    deliverables_html = _deliverables_html(slug)
    tier_html = _tier_includes_html(product.get("price", ""))

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{product.get('name','')} — AI Automated Systems</title>
<meta name="description" content="{ (product.get('offer') or product.get('pain') or '')[:160] }">
<style>
:root{{--bg:#0d0d0f;--card:#1a1a1e;--accent:#6366f1;--text:#e4e4e7;--muted:#a1a1aa;--border:#27272a;--price:#34d399}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6}}
.container{{max-width:880px;margin:0 auto;padding:2.5rem 1.5rem}}
header a{{color:var(--accent);text-decoration:none}}
.img{{width:100%;max-height:340px;object-fit:cover;border-radius:12px;border:1px solid var(--border);margin:1.25rem 0}}
.badge{{display:inline-block;padding:.2rem .6rem;border-radius:6px;font-size:.75rem;font-weight:700;text-transform:uppercase;background:#166534;color:#4ade80}}
.price{{color:var(--price);font-weight:700;font-size:1.6rem;margin:.5rem 0}}
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
.tbadge{{display:flex;gap:.6rem;align-items:center;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:.7rem .9rem}}
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
.sticky-cta{{position:fixed;bottom:0;left:0;right:0;background:var(--card);border-top:1px solid var(--border);padding:.8rem 1rem;display:flex;gap:.6rem;justify-content:center;z-index:50}}
@media(max-width:600px){{.sticky-cta{{flex-wrap:wrap}}}}
.exit-modal{{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;z-index:60}}
.exit-modal.show{{display:flex}}
.exit-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:2rem;max-width:420px;text-align:center}}
.exit-card input{{width:100%;padding:.6rem;margin:.6rem 0;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)}}
</style></head><body><div class="container">
<!-- SEO: JSON-LD Product -->
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"Product","name":"{product.get('name','')}","description":{(product.get('offer') or product.get('pain') or '')[:160]!r},"offers":{{"@type":"Offer","price":"{(product.get('price') or '').replace('$','').split('/')[0].strip()}","priceCurrency":"USD"}}}}
</script>
<h1>{product.get('name','')}</h1>
<span class="badge">{product.get('status','draft')}</span>
{ f'<img class="img" src="{img}" alt="{product.get("name","")}">' if img else '' }
{ f'<p class="price">{product.get("price","")}</p>' if product.get("price") else '' }
{ f'<p><b>For:</b> {product.get("audience","")}</p>' if product.get("audience") else '' }
{ f'<p class="pain">{product.get("pain","")}</p>' if product.get("pain") else '' }
{ f'<h2>What you get</h2><p>{product.get("offer","")}</p>' if product.get("offer") else '' }
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
    checkout = product.get("checkout_url") or ""
    if has_free:
        cta_html += '<a class="cta secondary" href="/p/{slug}/free" data-slug="{slug}">🎁 Start free →</a>'.format(slug=slug)
    if product.get("status") == "ready":
        if checkout.startswith("http"):
            cta_html += f'<a class="cta" href="{checkout}" target="_blank" rel="noopener" data-slug="{slug}">⚡ Get Pro →</a>'
        elif checkout and "contact" in checkout.lower():
            cta_html += f'<a class="cta" href="/contact?product={slug}" data-slug="{slug}">📩 Contact for pricing →</a>'
        else:
            # No usable checkout: route to contact so the lead is never lost (no dead end).
            cta_html += f'<a class="cta" href="/contact?product={slug}" data-slug="{slug}">📩 Get access →</a>'
        if product.get("gumroad_url"):
            cta_html += f'<a class="cta upgrade" href="{product["gumroad_url"]}" target="_blank" rel="noopener" data-slug="{slug}">⬆ Also on Gumroad →</a>'
        if has_enterprise:
            cta_html += f'<a class="cta enterprise" href="/contact?product={slug}">🏢 Talk to Enterprise →</a>'
    else:
        cta_html += f'<a class="cta" href="/p/{slug}">Contact / Details</a>'
    html += f'<div class="cta-row">{cta_html}</div>'
    html += """<footer>
AI Automated Systems · <a href="/legal/terms-of-service">Terms</a> · <a href="/legal/privacy-policy">Privacy</a> · <a href="/legal/refund-policy">Refunds</a>
</footer></div>
<script>
// Sticky CTA mirrors the in-page CTAs for mobile/no-scroll conversion.
(function(){{
  var row=document.querySelector('.cta-row');
  if(row){{var bar=document.createElement('div');bar.className='sticky-cta';bar.innerHTML=row.innerHTML;document.body.appendChild(bar);}}
  // Exit-intent email capture (lead magnet, not a dead form).
  var fired=false;
  function showExit(){{
    if(fired)return;fired=true;
    var m=document.createElement('div');m.className='exit-modal show';
    m.innerHTML='<div class=\"exit-card\"><h3>Wait — grab the free AI Ops Checklist</h3><p>Get the local AI ops checklist + weekly lab tips.</p><input id=\"exit-email\" placeholder=\"you@email.com\" type=\"email\"><button onclick=\"exitSubmit()\" style=\"padding:.6rem 1.2rem;border-radius:8px;border:0;background:#6366f1;color:#fff;font-weight:700;cursor:pointer\">Send it →</button></div>';
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
<style>body{{font-family:system-ui;background:#0d0d0f;color:#e4e4e7;max-width:620px;margin:8vh auto;padding:0 20px;line-height:1.6}}
h1{{font-size:1.9rem}} .muted{{color:#a1a1aa}} .card{{background:#1a1a1e;border:1px solid #27272a;border-radius:14px;padding:2rem}}
input{{width:100%;padding:.8rem;margin:.5rem 0;border-radius:8px;border:1px solid #27272a;background:#0d0d0f;color:#e4e4e7;font-size:1rem}}
button{{width:100%;padding:.9rem;border:0;border-radius:10px;background:#6366f1;color:#fff;font-weight:700;font-size:1rem;cursor:pointer;margin-top:.8rem}}
a{{color:#6366f1}}</style></head><body><div class='card'>
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
    subj = f"Enterprise inquiry — {product}" if product else "Enterprise inquiry"
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Enterprise — AI Automated Systems</title>
<style>body{{font-family:system-ui;background:#0d0d0f;color:#e4e4e7;max-width:620px;margin:8vh auto;padding:0 20px;line-height:1.6}}
h1{{font-size:1.9rem}} .muted{{color:#a1a1aa}} .card{{background:#1a1a1e;border:1px solid #27272a;border-radius:14px;padding:2rem}}
input,textarea{{width:100%;padding:.8rem;margin:.5rem 0;border-radius:8px;border:1px solid #27272a;background:#0d0d0f;color:#e4e4e7;font-size:1rem}}
button{{width:100%;padding:.9rem;border:0;border-radius:10px;background:#0ea5e9;color:#fff;font-weight:700;font-size:1rem;cursor:pointer;margin-top:.8rem}}
a{{color:#6366f1}}</style></head><body><div class='card'>
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
    body:JSON.stringify({{name:name,email:email,needs:needs,product:'{product}'}})}});
  document.getElementById('cmsg').textContent = r.ok ? '✅ Sent — we will reply within 1 business day.' : 'Try again.';
}});
</script>
<p class='muted'><a href='/'>← Back to store</a></p>
</div></body></html>"""
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
    products = store.list_products(settings.db_path)
    return {"products": products, "count": len(products)}


@app.post("/api/lead")
async def api_lead(payload: dict = Body(default={})):
    """Capture a lead (exit-intent, contact form, waitlist). Fail-soft."""
    import sqlite3 as _sql
    email = (payload.get("email") or "").strip()
    slug = payload.get("product_slug") or ""
    source = payload.get("source") or "unknown"
    if not email or "@" not in email:
        return {"ok": False, "reason": "invalid_email"}
    try:
        db = _sql.connect(settings.db_path)
        db.execute("""CREATE TABLE IF NOT EXISTS leads(
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, product_slug TEXT,
            source TEXT, notes TEXT, status TEXT DEFAULT 'new', created_at TEXT,
            tag TEXT)""")
        db.execute("INSERT OR IGNORE INTO leads(email,product_slug,source,status,created_at) VALUES(?,?,?,?,?)",
                   (email, slug, source, "new", datetime.datetime.now(datetime.timezone.utc).isoformat()))
        db.commit(); db.close()
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True}


@app.post("/api/contact")
async def api_contact(payload: dict = Body(default={})):
    """Enterprise/contact intake. Stores lead + fires Telegram alert. Fail-soft."""
    import sqlite3 as _sql, subprocess
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()
    needs = (payload.get("needs") or "").strip()
    slug = payload.get("product") or ""
    if not email or "@" not in email:
        return {"ok": False, "reason": "invalid_email"}
    try:
        db = _sql.connect(settings.db_path)
        db.execute("""CREATE TABLE IF NOT EXISTS leads(
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, product_slug TEXT,
            source TEXT, notes TEXT, status TEXT DEFAULT 'new', created_at TEXT, tag TEXT)""")
        db.execute("INSERT OR IGNORE INTO leads(email,product_slug,source,notes,status,created_at) VALUES(?,?,?,?,?,?)",
                   (email, slug, "contact", f"{name}: {needs}"[:500], "new", datetime.datetime.now(datetime.timezone.utc).isoformat()))
        db.commit(); db.close()
        msg = f"📩 New contact: {name} <{email}> product={slug} — {needs[:120]}"
        subprocess.run(['/home/scott/ai-lab/scripts/bin/telegram-alert.sh', msg], stderr=subprocess.DEVNULL)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True}


@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap():
    products = store.list_products(settings.db_path)
    urls = ["https://aiautomatedsystems.ca/", "https://aiautomatedsystems.ca/pricing",
            "https://aiautomatedsystems.ca/blog", "https://aiautomatedsystems.ca/landing/gpu-compute-waitlist.html"]
    for p in products:
        slug = p.get("slug")
        if slug:
            urls.append(f"https://aiautomatedsystems.ca/p/{slug}")
            urls.append(f"https://aiautomatedsystems.ca/landing/{slug}.html")
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + \
           "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>"
    return body


@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page():
    products = store.list_products(settings.db_path)
    rows = []
    for p in products:
        if p.get("status") != "ready":
            continue
        price = p.get("price") or ""
        checkout = p.get("checkout_url") or ""
        cta = f"<a class='cta' href='{checkout}'>Buy — {price}</a>" if checkout.startswith("http") \
            else f"<a class='cta' href='/contact?product={p.get('slug')}'>Contact</a>"
        rows.append(f"<tr><td><a href='/p/{p.get('slug')}'>{p.get('name')}</a></td><td>{price}</td><td>{cta}</td></tr>")
    table = "\n".join(rows)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Pricing — AI Automated Systems</title>
<style>body{{font-family:system-ui;background:#0d0d0f;color:#e4e4e7;max-width:900px;margin:6vh auto;padding:0 20px;line-height:1.6}}
h1{{font-size:2rem}} table{{width:100%;border-collapse:collapse;margin-top:1rem}} th,td{{text-align:left;padding:.7rem;border-bottom:1px solid #27272a}}
.cta{{background:#0ea5e9;color:#fff;padding:.5rem .9rem;border-radius:8px;text-decoration:none;font-weight:700}}
a{{color:#6366f1}}</style></head><body>
<h1>💳 All products & bundles</h1>
<p class='muted'>One-time payments. Lifetime access. Stripe-secured. Need volume or custom? <a href='/contact'>Talk to us</a>.</p>
<table><thead><tr><th>Product</th><th>Price</th><th></th></tr></thead><tbody>
{table}
</tbody></table>
<p class='muted'><a href='/'>← Back to home</a></p>
</body></html>"""
    return html


@app.get("/metrics/funnel", response_class=PlainTextResponse)
async def funnel_metrics():
    """Conversion funnel: events -> leads -> purchases, real data."""
    import sqlite3 as _sql, json as _json
    db = _sql.connect(settings.db_path)
    events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    leads = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    purchases = db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
    rev = db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM purchases").fetchone()[0]
    db.close()
    data = {"events": events, "leads": leads, "purchases": purchases,
            "revenue_cents": rev, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    return _json.dumps(data)


@app.post("/api/privacy/erase")
async def privacy_erase(payload: dict = Body(default={})):
    """GDPR/CCPA erasure request. Removes lead + contact rows for an email. Fail-soft."""
    import sqlite3 as _sql
    email = (payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return {"ok": False, "reason": "invalid_email"}
    try:
        db = _sql.connect(settings.db_path)
        cur = db.execute("DELETE FROM leads WHERE lower(email)=?", (email,))
        n = cur.rowcount
        db.commit(); db.close()
        return {"ok": True, "erased_rows": n}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


@app.get("/blog", response_class=HTMLResponse)
async def blog_index():
    from pathlib import Path as _P
    import html as _h
    drafts = sorted(_P('/home/scott/ai-lab/reports/content/drafts').glob('*.md'), reverse=True) if _P('/home/scott/ai-lab/reports/content/drafts').exists() else []
    items = []
    for d in drafts[:30]:
        title = d.read_text().splitlines()[0].lstrip('# ').strip() if d.read_text() else d.stem
        slug = d.stem
        items.append(f"<li><a href='/blog/{slug}'>{_h.escape(title)}</a></li>")
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>Blog — AI Automated Systems</title>
<style>body{{font-family:system-ui;background:#0d0d0f;color:#e4e4e7;max-width:800px;margin:6vh auto;padding:0 20px;line-height:1.7}}
h1{{font-size:2rem}} a{{color:#6366f1}} li{{margin:.5rem 0}}</style></head><body>
<h1>📝 Local-AI Ops Blog</h1>
<p class='muted'>Practical guides on self-hosting, ComfyUI, n8n, and private inference.</p>
<ul>{''.join(items)}</ul>
<p class='muted'><a href='/'>← Home</a> · <a href='/pricing'>Pricing</a></p>
</body></html>"""
    return html


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(slug: str):
    from pathlib import Path as _P
    import html as _h, re as _re
    p = _P(f'/home/scott/ai-lab/reports/content/drafts/{slug}.md')
    if not p.exists():
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    md = p.read_text()
    # minimal md->html
    out = []
    for line in md.splitlines():
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
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>{_h.escape(slug)} — AI Automated Systems</title>
<style>body{{font-family:system-ui;background:#0d0d0f;color:#e4e4e7;max-width:800px;margin:6vh auto;padding:0 20px;line-height:1.7}}
h1,h2{{color:#fff}} a{{color:#6366f1}} p,li{{color:#d4d4d8}}</style></head><body>
{body}
<p class='muted'><a href='/blog'>← All posts</a></p>
</body></html>"""
    return html


@app.post("/api/track")
async def track_event(payload: dict = Body(default={}), request: Request = None):
    """Local-first analytics ingestion. Never throws — best-effort record."""
    event = payload.get("event") if isinstance(payload, dict) else None
    page = payload.get("page")
    slug = payload.get("slug")
    referrer = (request.headers.get("referer") if request else None)
    if not event:
        return {"status": "ok"}
    _record_event(
        event, page=page, product_slug=slug,
        checkout_url=None, session_id=_session_id(request) if request else "anon",
        referrer=referrer,
    )
    return {"status": "ok"}


@app.get("/api/gpu-status")
async def gpu_status():
    return _gpu_status()


@app.get("/api/analytics")
async def analytics(x_api_key: Optional[str] = Header(None)):
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
    email = _validate_email(payload.email)
    _record_event(
        "subscribe", page=request.url.path, product_slug=None,
        checkout_url=None, session_id=None, referrer=request.headers.get("referer"),
    )
    return {"status": "ok", "email": email, "tag": payload.tag}


# ── Admin / internal reads ─────────────────────────────────────────────────────

@app.get("/api/leads")
async def list_leads(x_api_key: Optional[str] = Header(None)):
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
