"""Analytics ingestion, platform truth, and conversion reporting."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.core.config import require_operator, settings
from app.funnel_truth import funnel_summary, record_funnel_event
from app.middleware.request_context import get_session_id, get_traffic_class
from app.services.analytics_service import get_analytics_summary, record_event

router = APIRouter(tags=["Analytics & Truth"])
logger = logging.getLogger("storefront.analytics_api")


@router.post("/api/track")
@router.post("/api/analytics/event")
async def track_event(payload: dict = Body(default={}), request: Request = None):
    """Local-first privacy-preserving analytics ingestion."""
    consent = request.cookies.get("hardonia_consent") if request else None
    event = payload.get("event") if isinstance(payload, dict) else None

    # Respect explicit opt-out of non-essential analytics
    if consent == "declined" and event not in ("purchase", "checkout_redirect", "download"):
        return {"status": "ok", "note": "analytics_consent_declined"}

    if not event:
        return {"status": "ok"}

    session_id = get_session_id(request) if request else "anon"
    traffic_class = get_traffic_class(request) if request else "unknown"

    record_event(
        event,
        page=payload.get("page"),
        product_slug=payload.get("slug"),
        session_id=session_id,
        referrer=request.headers.get("referer") if request else None,
        traffic_class=traffic_class,
    )

    # Record into privacy funnel schema
    effective_analytics = settings.effective_analytics_db_path
    if effective_analytics:
        try:
            record_funnel_event(
                effective_analytics,
                stage="landing" if event == "page_view" else "offer_click",
                classification=traffic_class if traffic_class in ("synthetic", "likely_bot") else "unknown",
                classification_reason="request_classified",
                referrer=request.headers.get("referer") if request else None,
                campaign=payload.get("utm_campaign"),
                page=payload.get("page"),
                product=payload.get("slug"),
                consent=consent or "unset",
            )
        except Exception as e:
            logger.warning("Funnel recording failed: %s", e)

    return {"status": "ok"}


@router.get("/api/analytics")
async def get_analytics(_: None = Depends(require_operator)):
    """Operator-only analytics overview."""
    return get_analytics_summary(settings.effective_analytics_db_path)


@router.get("/api/platform-truth")
async def platform_truth_api():
    """Observable platform verification telemetry."""
    summary = funnel_summary(settings.effective_analytics_db_path)
    return {
        "platform": "Hardonia / AI Automated Systems",
        "verification": "deterministic-air-gapped",
        "funnel_truth": summary,
        "operator_telemetry": "local-sqlite",
    }


@router.get("/platform-truth", response_class=HTMLResponse)
@router.get("/trust", response_class=HTMLResponse)
async def platform_truth_page():
    """Public platform truth and verified operation principles."""
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Platform Truth & Sovereign Principles — AI Automated Systems</title>
<style>
:root{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:760px;margin:6vh auto;padding:0 20px;line-height:1.7}
h1{font-size:2.2rem;letter-spacing:-.02em}
.principle{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.5rem;margin:1.5rem 0}
h3{color:var(--accent);margin-bottom:.4rem}
a{color:var(--accent);text-decoration:none}
</style></head>
<body>
<p><a href='/'>← Storefront Home</a></p>
<h1>Platform Truth & Sovereign Principles</h1>
<p style='color:var(--muted)'>How AI Automated Systems engineers air-gapped, verifiable AI tooling without commercial compromises.</p>

<div class='principle'>
  <h3>1. Local-First Execution & Zero Telemetry</h3>
  <p>All client deliverables run completely offline or on dedicated bare metal. We never retain, transmit, or inspect your prompts, notes, or business records.</p>
</div>

<div class='principle'>
  <h3>2. Deterministic Governance & Audit Trails</h3>
  <p>Our document drafting engines (Sentinel, Ops, Ledger, HR) utilize reproducible citation matrices and cryptographic git commit hashes for every change.</p>
</div>

<div class='principle'>
  <h3>3. Transparent, Fixed Pricing</h3>
  <p>No per-seat escalation extortion. Once deployed, our software is yours to run continuously.</p>
</div>

<p><a href='/pricing'>Explore Software Catalog →</a> · <a href='/status'>Live System Status →</a></p>
</body></html>"""
    return HTMLResponse(html)


@router.get("/metrics/funnel", response_class=PlainTextResponse)
async def funnel_metrics_text(_: None = Depends(require_operator)):
    """Operator-only funnel metrics formatted for monitoring."""
    summary = funnel_summary(settings.effective_analytics_db_path)
    lines = [
        "# Privacy-Preserving Funnel Metrics",
        f"schema: {summary.get('schema')}",
        "---",
    ]
    for stage, count in summary.get("commercial_stage_counts", {}).items():
        lines.append(f"stage_{stage}: {count}")
    return "\n".join(lines)
