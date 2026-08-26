"""Sovereign Private AI Operations landing, synthetic demo, and static report routes."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.config import settings

router = APIRouter(tags=["Private AI Operations"])

_LANDING_SLUG_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,120}$")


@router.get("/private-ai-operations", response_class=HTMLResponse)
async def private_ai_operations_landing():
    """Sovereign private AI operations evaluation page."""
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Private AI Operations — AI Automated Systems</title>
  <meta name="description" content="Sovereign, air-gapped AI operations, deterministic workflows, and private infrastructure.">
  <link rel="canonical" href="https://aiautomatedsystems.ca/private-ai-operations">
  <style>
    :root { --bg: #f5f1e8; --card: #fffdf8; --accent: #0f766e; --text: #1f2933; --muted: #66717d; --border: #d8d3ca; }
    body { font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); max-width: 800px; margin: 5vh auto; padding: 0 20px; line-height: 1.6; }
    .panel { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 2.5rem; box-shadow: 0 12px 30px rgba(31,41,51,.06); }
    h1 { font-size: 2.4rem; letter-spacing: -.03em; }
    .badge-warning { display: inline-block; padding: .2rem .6rem; background: #fef3c7; color: #92400e; font-weight: 700; border-radius: 4px; font-size: .8rem; margin-bottom: 1rem; }
    a { color: var(--accent); text-decoration: none; }
  </style>
</head>
<body>
<p><a href="/">← Home</a></p>
<div class="panel">
  <span class="badge-warning">NOT LIVE — EVALUATION ONLY</span>
  <h1>Private AI Operations</h1>
  <p style="color:var(--muted);font-size:1.15rem">Architectural governance and zero-telemetry operations for sovereign AI stacks.</p>
  <hr style="border:0;border-top:1px solid var(--border);margin:1.5rem 0">
  <h3>Pricing hypothesis & deployment options</h3>
  <p>We evaluate custom on-premise compute and model isolation topologies under strict non-disclosure terms.</p>
  <p><a style="display:inline-block;padding:.75rem 1.4rem;background:var(--accent);color:#fff;border-radius:8px;font-weight:700" href="/contact">Request a scoped evaluation →</a></p>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/private-ai-operations-demo/", response_class=HTMLResponse)
async def private_ai_operations_demo_index():
    """Synthetic demo surface for private AI operations."""
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,follow">
  <title>Synthetic Demo — Private AI Operations</title>
  <link rel="stylesheet" href="/private-ai-operations-demo/styles.css">
</head>
<body>
<div style="font-family:system-ui;padding:2rem;max-width:700px;margin:0 auto">
  <h1>Private AI Operations Synthetic Demo</h1>
  <p>Synthetic demo data for evaluation purposes only.</p>
  <script src="/private-ai-operations-demo/app.js"></script>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/private-ai-operations-demo/{asset}")
async def private_ai_operations_demo_asset(asset: str):
    """Serve allowlisted demo synthetic assets."""
    if ".." in asset or "/" in asset or "\\" in asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    if asset == "styles.css":
        return Response("body { background: #f5f1e8; color: #1f2933; }", media_type="text/css")
    elif asset == "app.js":
        return Response("console.log('Synthetic Demo Loaded');", media_type="application/javascript")
    elif asset == "demo-data.json":
        return JSONResponse({
            "data_classification": "synthetic-demo-only",
            "deterministic": True,
            "sample_records": [
                {"id": "synth_1", "status": "verified", "mode": "air-gapped"}
            ]
        })
    else:
        raise HTTPException(status_code=404, detail="Asset not found")


@router.get("/landing/{slug}.html", response_class=HTMLResponse)
async def landing_page(slug: str):
    """Serve statically generated landing reports with path traversal protection."""
    if ".." in slug or "/" in slug or "\\" in slug or not _LANDING_SLUG_REGEX.match(slug):
        raise HTTPException(status_code=400, detail="Invalid landing path")

    base = Path(settings.landing_dir).resolve()
    target = (base / f"{slug}.html").resolve()

    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid landing path") from None

    if not target.is_file():
        raise HTTPException(status_code=404, detail="Landing page not found")

    return HTMLResponse(target.read_text(encoding="utf-8"))
