"""Search engine optimization, LLM discoverability, and verification routes."""

from __future__ import annotations

import os
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import PlainTextResponse

from app import store
from app.core.config import public_brand, settings

router = APIRouter(tags=["SEO & Verification"])


@router.get("/robots.txt", response_class=PlainTextResponse)
@router.head("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request):
    """Robots exclusion standard directive."""
    site_base, _ = public_brand(request)
    return f"""User-agent: *
Allow: /
Disallow: /api/
Disallow: /download/
Disallow: /order/

Sitemap: {site_base}/sitemap.xml
"""


@router.get("/sitemap.xml", response_class=Response)
@router.head("/sitemap.xml", response_class=Response)
async def sitemap_xml(request: Request):
    """Dynamically generated XML sitemap."""
    site_base, _ = public_brand(request)
    products = store.list_products(settings.db_path)

    urls = [
        f"<url><loc>{site_base}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>",
        f"<url><loc>{site_base}/pricing</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>",
        f"<url><loc>{site_base}/proof-score</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>",
        f"<url><loc>{site_base}/blog</loc><changefreq>weekly</changefreq><priority>0.7</priority></url>",
        f"<url><loc>{site_base}/status</loc><changefreq>daily</changefreq><priority>0.6</priority></url>",
        f"<url><loc>https://aiautomatedsystems.ca/private-ai-operations</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>",
    ]

    for p in products:
        slug = p.get("slug")
        if slug:
            urls.append(
                f"<url><loc>{site_base}/p/{_xml_escape(slug)}</loc><changefreq>weekly</changefreq><priority>0.9</priority></url>"
            )

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{''.join(urls)}
</urlset>"""
    return Response(xml, media_type="application/xml")


@router.get("/llms.txt", response_class=PlainTextResponse)
@router.head("/llms.txt", response_class=PlainTextResponse)
async def llms_txt(request: Request):
    """Plain-text sitemap and system specification optimized for LLM agent discovery."""
    site_base, site_name = public_brand(request)
    products = store.list_products(settings.db_path)

    lines = [
        f"# {site_name} — Sovereign Mission Intelligence",
        "",
        "> Autonomous, air-gapped AI software, local workflows, and deterministic governance suites.",
        "",
        "## Products & Tools",
    ]

    for p in products:
        lines.append(f"- [{p['name']}]({site_base}/p/{p['slug']}): {p.get('offer', p.get('pain', ''))} ({p.get('price', '')})")

    lines.extend([
        "",
        "## Core Capabilities",
        f"- [Pricing]({site_base}/pricing): Fixed-price air-gapped software suites.",
        f"- [Proof Score]({site_base}/proof-score): Verifiable hardware and zero-telemetry benchmarks.",
        f"- [System Status]({site_base}/status): Observable GPU farm telemetry.",
        "- [Private AI Operations](https://aiautomatedsystems.ca/private-ai-operations): Autonomous operations evaluation framework.",
        "",
        "## Contact & Integration",
        f"- [Talk to an Operator]({site_base}/contact)",
    ])

    return "\n".join(lines)


@router.post("/csp-report")
async def csp_report(report: dict = Body(default={})):
    """Ingest browser Content-Security-Policy violation reports."""
    return {"status": "ok"}


@router.get("/google9bd18844eac022ef.html", response_class=Response)
@router.head("/google9bd18844eac022ef.html", response_class=Response)
async def google_verification():
    """Google search console root verification."""
    return Response("google-site-verification: google9bd18844eac022ef.html", media_type="text/html; charset=utf-8")


@router.get("/{key}.txt", response_class=PlainTextResponse)
@router.head("/{key}.txt", response_class=PlainTextResponse)
async def indexnow_verification(key: str):
    """IndexNow dynamic verification key file matching configured or static keys."""
    env_key = os.environ.get("INDEXNOW_KEY", settings.indexnow_key)
    if env_key and key == env_key:
        return Response(env_key, media_type="text/plain; charset=utf-8")

    # Check static directory for key file
    static_file = Path(settings.static_dir) / f"{key}.txt"
    if static_file.is_file():
        return Response(static_file.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")

    return PlainTextResponse("Not found", status_code=404)
