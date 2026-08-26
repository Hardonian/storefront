"""Lead capture, newsletter subscriptions, and privacy management routes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app import store
from app.core.config import require_operator, settings
from app.core.database import get_db
from app.core.security import build_download_url, validate_email_address, validate_slug
from app.funnel_truth import record_funnel_event
from app.middleware.rate_limiter import check_rate_limit, get_client_ip
from app.services.analytics_service import record_event

router = APIRouter(tags=["Leads & Subscriptions"])
logger = logging.getLogger("storefront.leads")


class LeadCreate(BaseModel):
    email: str = Field(..., max_length=254)
    product_slug: str | None = Field(default=None, max_length=120)
    source: str = Field(default="landing", max_length=64)
    notes: str | None = Field(default=None, max_length=500)


class SubscribeCreate(BaseModel):
    email: str = Field(..., max_length=254)
    tag: str | None = Field(default=None, max_length=64)
    website: str | None = Field(default=None, max_length=254)  # Honeypot field


class UnsubscribeRequest(BaseModel):
    email: str = Field(..., max_length=254)


class PrivacyEraseRequest(BaseModel):
    email: str = Field(..., max_length=254)


@router.post("/api/leads")
@router.post("/api/lead")
async def create_lead(payload: LeadCreate, request: Request):
    """Capture prospect leads with rate-limiting and email validation."""
    check_rate_limit(request)
    email = validate_email_address(payload.email)
    slug = validate_slug(payload.product_slug) if payload.product_slug else None

    store.create_lead(
        email=email,
        product_slug=slug,
        source=payload.source,
        notes=payload.notes,
        db_path=settings.db_path,
    )

    record_event(
        "lead",
        page=request.url.path,
        product_slug=slug,
        referrer=request.headers.get("referer"),
    )

    # Record into privacy-funnel truth schema
    effective_analytics = settings.effective_analytics_db_path
    if effective_analytics:
        try:
            record_funnel_event(
                effective_analytics,
                stage="lead_start",
                classification="unknown",
                classification_reason="request_classified",
                referrer=request.headers.get("referer"),
                page=request.url.path,
                product=slug,
                consent="unset",
            )
        except Exception as e:
            logger.warning("Funnel recording failed: %s", e)

    resp: dict[str, Any] = {"status": "ok", "email": email}

    # Generate signed download URL for free trials if bundle exists
    if payload.source == "free_trial" and slug:
        bundle = Path(settings.bundles_dir) / f"{slug}.zip"
        if bundle.exists():
            resp["download_url"] = build_download_url(slug, ttl_seconds=86400)

    return resp


@router.post("/api/subscribe")
async def subscribe(payload: SubscribeCreate, request: Request):
    """Newsletter subscription endpoint with honeypot bot trap."""
    check_rate_limit(request)

    # Honeypot trap: if website field is filled, silently discard without error
    if payload.website:
        return {"status": "ok", "email": payload.email, "tag": payload.tag}

    email = validate_email_address(payload.email)
    tag = payload.tag or "newsletter"

    store.create_lead(
        email=email,
        product_slug=None,
        source="newsletter",
        notes=f"tag={tag}",
        tag=tag,
        db_path=settings.db_path,
    )

    record_event(
        "subscribe",
        page=request.url.path,
        referrer=request.headers.get("referer"),
    )

    effective_analytics = settings.effective_analytics_db_path
    if effective_analytics:
        try:
            record_funnel_event(
                effective_analytics,
                stage="lead_start",
                classification="unknown",
                classification_reason="request_classified",
                referrer=request.headers.get("referer"),
                page=request.url.path,
                product=None,
                consent="unset",
            )
        except Exception as e:
            logger.warning("Funnel recording failed: %s", e)

    return {"status": "ok", "email": email, "tag": tag}


@router.get("/api/leads")
async def list_leads(_: None = Depends(require_operator)):
    """Operator-only lead retrieval."""
    leads = store.list_leads(settings.db_path)
    return {"leads": leads}


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page():
    """Unsubscribe confirmation page."""
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Unsubscribe — AI Automated Systems</title>
<style>
:root{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:540px;margin:10vh auto;padding:0 20px;line-height:1.6}
.box{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:2rem;box-shadow:0 12px 30px rgba(31,41,51,.06)}
input{width:100%;padding:.75rem 1rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:1rem;margin:1rem 0}
button{padding:.8rem 1.4rem;background:var(--accent);color:#fff;border:0;border-radius:8px;font-weight:700;cursor:pointer}
a{color:var(--accent);text-decoration:none}
</style></head>
<body>
<div class='box'>
<h2>Manage Email Preferences</h2>
<p style='color:var(--muted)'>Enter your email address below to unsubscribe from our newsletter and updates.</p>
<form id='unsub-form'>
  <input type='email' id='unsub-email' placeholder='your@email.com' required>
  <button type='submit'>Unsubscribe</button>
</form>
<div id='unsub-msg' style='margin-top:1rem;font-weight:600'></div>
</div>
<script>
document.getElementById('unsub-form').addEventListener('submit', function(e) {
  e.preventDefault();
  var email = document.getElementById('unsub-email').value.trim();
  var msg = document.getElementById('unsub-msg');
  msg.textContent = 'Processing…';
  fetch('/api/unsubscribe', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email: email})
  }).then(function(r) { return r.json(); })
  .then(function() {
    msg.textContent = '✅ You have been successfully unsubscribed.';
    document.getElementById('unsub-form').reset();
  }).catch(function() {
    msg.textContent = 'Error processing request. Please try again.';
  });
});
</script>
</body></html>"""
    return HTMLResponse(html)


@router.post("/api/unsubscribe")
async def api_unsubscribe(payload: UnsubscribeRequest):
    """Mark a lead as unsubscribed in SQLite."""
    email = validate_email_address(payload.email)
    with get_db(settings.db_path) as conn:
        conn.execute("UPDATE leads SET status = 'unsubscribed' WHERE email = ?", (email,))
    return {"status": "ok", "email": email}


@router.post("/api/privacy/erase")
async def privacy_erase(payload: PrivacyEraseRequest):
    """Erase user email records across leads for privacy compliance."""
    email = validate_email_address(payload.email)
    with get_db(settings.db_path) as conn:
        conn.execute("DELETE FROM leads WHERE email = ?", (email,))
    return {"status": "ok", "erased": email}
