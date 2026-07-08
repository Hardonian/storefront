"""
Storefront — public-facing product catalog & lead capture microservice.

Run:  ./run.sh  (or uvicorn app.main:app --host 0.0.0.0 --port 8020)
"""

from __future__ import annotations

import time
import re
from pathlib import Path
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, EmailStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import store

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


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "storefront", "version": app.version}


@app.get("/", response_class=HTMLResponse)
async def index():
    products = store.list_products(settings.db_path)
    tmpl = jinja_env.get_template("index.html")
    html = tmpl.render(products=products, title="AI Products — Storefront")
    return HTMLResponse(content=html)


@app.get("/p/{slug}", response_class=HTMLResponse)
async def product_landing(slug: str):
    """Serve a static landing page by product slug."""
    # Sanitise slug to prevent path traversal
    slug_safe = re.sub(r"[^a-zA-Z0-9_\-]", "", slug)
    if slug_safe != slug:
        raise HTTPException(status_code=400, detail="Invalid slug")

    html_path = LANDING_DIR / f"{slug_safe}.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail=f"Landing page '{slug}' not found")
    return FileResponse(str(html_path), media_type="text/html")


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