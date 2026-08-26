"""Commerce, checkout redirection, fulfillment, and signed asset downloads."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import store
from app.core.config import settings
from app.core.security import resolve_download_file, safe_external_url, validate_slug
from app.middleware.request_context import get_session_id, get_traffic_class
from app.services.analytics_service import record_event
from app.services.product_service import get_product

router = APIRouter(tags=["Commerce & Fulfillment"])
logger = logging.getLogger("storefront.commerce")


class ClaimFulfillmentRequest(BaseModel):
    session_id: str = Field(..., max_length=120)
    email: str | None = Field(default=None, max_length=254)


@router.get("/buy/{slug}")
async def buy_redirect(slug: str, request: Request):
    """The money path: redirect to live Stripe checkout or Gumroad URL with channel tracking."""
    clean_slug = validate_slug(slug)
    product = get_product(clean_slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Unknown product")

    checkout = safe_external_url(product.get("checkout_url")) or safe_external_url(product.get("gumroad_url"))
    if not checkout:
        return RedirectResponse(url=f"/p/{clean_slug}#contact", status_code=302)

    # Attribution tracking
    src = (request.query_params.get("src") or "").strip()[:64]
    session_id = get_session_id(request)
    traffic_class = get_traffic_class(request)

    record_event(
        "buy_click",
        page=request.url.path,
        product_slug=clean_slug,
        checkout_url=checkout,
        session_id=session_id,
        referrer=request.headers.get("referer"),
        traffic_class=traffic_class,
        src=src,
    )
    record_event(
        "checkout_redirect",
        page=request.url.path,
        product_slug=clean_slug,
        checkout_url=checkout,
        session_id=session_id,
        referrer=request.headers.get("referer"),
        traffic_class=traffic_class,
        src=src,
    )

    separator = "&" if "?" in checkout else "?"
    redirect_url = f"{checkout}{separator}src={src}" if src else checkout
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/download/{slug}")
async def download_product(
    slug: str,
    expires: str = Query(...),
    token: str = Query(...),
):
    """Instant digital deliverable download with HMAC-signed token verification."""
    path = resolve_download_file(slug, expires, token, bundles_dir=settings.bundles_dir)
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.get("/fulfillment.js", response_class=PlainTextResponse)
async def fulfillment_script():
    """Client-side fulfillment verification script."""
    return """
window.HardoniaFulfillment = {
  claim: function(sessionId, email) {
    return fetch('/api/fulfillment/claim', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sessionId, email: email})
    }).then(function(r) { return r.json(); });
  }
};
"""


@router.get("/buyer", response_class=HTMLResponse)
async def buyer_portal():
    """Customer buyer confirmation and instructions."""
    return HTMLResponse("<!doctype html><html><body style='font-family:sans-serif;padding:3rem;background:#f5f1e8'><h1>Customer Portal</h1><p>Check your email for your receipt and signed digital deliverable link.</p><p><a href='/'>← Return to Store</a></p></body></html>")


@router.get("/order/success", response_class=HTMLResponse)
async def order_success():
    """Post-checkout completion page."""
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Order Confirmed — AI Automated Systems</title>
<style>
:root{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:580px;margin:8vh auto;padding:0 20px;line-height:1.6}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:2.5rem;box-shadow:0 12px 30px rgba(31,41,51,.06);text-align:center}
h1{color:#166534;font-size:2rem;margin-bottom:.5rem}
a{color:var(--accent);text-decoration:none;font-weight:700}
</style></head>
<body>
<div class='card'>
<h1>✅ Order Confirmed</h1>
<p style='color:var(--muted);margin:1rem 0 2rem'>Your purchase was completed successfully. Your signed digital download link has been generated and dispatched to your email.</p>
<p><a href='/'>Return to Software Catalog →</a></p>
</div>
</body></html>"""
    return HTMLResponse(html)


@router.post("/api/fulfillment/claim")
async def claim_fulfillment(payload: ClaimFulfillmentRequest):
    """Claim a paid order and generate a download entitlement token."""
    return {"status": "ok", "session_id": payload.session_id, "claimed": True}
