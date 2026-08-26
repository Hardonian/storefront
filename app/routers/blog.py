"""Engineering field notes, local AI guides, and RSS feed."""

from __future__ import annotations

import html as _html
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.security import validate_slug

router = APIRouter(tags=["Blog & Field Notes"])
logger = logging.getLogger("storefront.blog")

STATIC_POSTS = [
    {
        "slug": "local-inference-guide",
        "title": "Running Sovereign Local LLMs: Zero-Telemetry Production Stack",
        "desc": "How to deploy air-gapped LLM endpoints with deterministic latency using Ollama, vLLM, and local SQLite audit trails.",
    },
    {
        "slug": "comfyui-production-pipeline",
        "title": "ComfyUI in Production: Freezing Node Topologies for Commercial Diffusion",
        "desc": "Best practices for deploying reproducible image diffusion workflows without update drift or broken custom nodes.",
    },
    {
        "slug": "self-hosted-automation-playbook",
        "title": "Replacing Cloud SaaS with Self-Hosted n8n & Hardened UFW Networks",
        "desc": "Practical guide to migrating high-volume business automations away from per-task SaaS fees to dedicated bare metal.",
    },
]


@router.get("/blog", response_class=HTMLResponse)
async def blog_index():
    """Blog index listing practical field notes and guides."""
    drafts_dir = Path(settings.content_drafts_dir)
    posts = []

    if drafts_dir.exists():
        for d in sorted(drafts_dir.glob("*.md"), reverse=True)[:30]:
            try:
                text = d.read_text(encoding="utf-8")
                title = text.splitlines()[0].lstrip("# ").strip() if text else d.stem
                posts.append({"slug": d.stem, "title": title, "desc": "Practical private-AI and local operations guide."})
            except Exception:
                pass

    if not posts:
        posts = STATIC_POSTS

    cards = []
    for p in posts:
        cards.append(
            f"<a class='post-card' href='/blog/{p['slug']}'>"
            f"<span class='post-title'>{_html.escape(p['title'])}</span>"
            f"<p style='color:var(--muted);font-size:.9rem;margin:.4rem 0'>{_html.escape(p.get('desc', ''))}</p>"
            f"<span class='post-cta'>Read guide →</span></a>"
        )
    cards_html = "".join(cards)

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Local AI Ops Blog & Field Notes — AI Automated Systems</title>
<meta name='description' content='Field-tested engineering guides for running private AI: ComfyUI, n8n, local inference, and sovereign AI strategy.'>
<link rel='canonical' href='https://aiautomatedsystems.ca/blog'>
<style>
:root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--accent-hover:#115e59;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:3rem 1.5rem;min-height:100vh}}
.container{{max-width:920px;margin:0 auto}}
.site-nav{{display:flex;justify-content:space-between;margin-bottom:3rem;font-size:.9rem}}
.site-nav a{{color:var(--muted);text-decoration:none;font-weight:600}}
h1{{font-size:2.8rem;letter-spacing:-.03em;margin-bottom:.5rem}}
.lead{{color:var(--muted);font-size:1.15rem;margin-bottom:2rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:1.25rem;margin:2rem 0}}
.post-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.5rem;text-decoration:none;color:var(--text);box-shadow:0 12px 30px rgba(31,41,51,.05);transition:transform .2s,border-color .2s;display:flex;flex-direction:column}}
.post-card:hover{{transform:translateY(-3px);border-color:var(--accent)}}
.post-title{{font-weight:700;font-size:1.1rem;line-height:1.35}}
.post-cta{{color:var(--accent);font-weight:700;font-size:.9rem;margin-top:auto;padding-top:.75rem}}
footer{{text-align:center;margin-top:3.5rem;color:var(--muted);font-size:.85rem}}
footer a{{color:var(--accent);text-decoration:none}}
</style></head>
<body>
<div class='container'>
<nav class='site-nav'><a href='/'>← Storefront</a><div><a href='/pricing'>Pricing</a> · <a href='/contact'>Contact</a></div></nav>
<h1>Local AI Ops Blog</h1>
<p class='lead'>Practical, field-tested guides for running private AI: local LLM inference, GPU operations, ComfyUI pipelines, and autonomous workflow architecture.</p>
<div class='grid'>{cards_html}</div>
<footer><p>AI Automated Systems · <a href='/legal/terms-of-service'>Terms</a> · <a href='/legal/privacy-policy'>Privacy</a> · <a href='/blog/rss.xml'>RSS Feed</a></p></footer>
</div>
</body></html>"""
    return HTMLResponse(html)


@router.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(slug: str):
    """Render single blog post."""
    clean_slug = validate_slug(slug)
    drafts_dir = Path(settings.content_drafts_dir)
    target = drafts_dir / f"{clean_slug}.md"

    if target.is_file():
        lines = target.read_text(encoding="utf-8").splitlines()
        title_raw = next((line[2:].strip() for line in lines if line.startswith("# ")), clean_slug.replace("-", " ").title())
        desc_raw = next((line.strip() for line in lines if line.strip() and not line.startswith("#")), title_raw)[:160]
        body_parts = []
        for line in lines:
            s = line.strip()
            if s.startswith("## "):
                body_parts.append(f"<h2>{_html.escape(s[3:])}</h2>")
            elif s.startswith("# "):
                body_parts.append(f"<h1>{_html.escape(s[2:])}</h1>")
            elif s.startswith("- "):
                body_parts.append(f"<li>{_html.escape(s[2:])}</li>")
            elif s:
                body_parts.append(f"<p>{_html.escape(s)}</p>")
        body = "\n".join(body_parts)
    else:
        # Check static posts fallback
        post_match = next((p for p in STATIC_POSTS if p["slug"] == clean_slug), None)
        if not post_match:
            raise HTTPException(status_code=404, detail="Post not found")
        title_raw = post_match["title"]
        desc_raw = post_match["desc"]
        body = f"<h1>{_html.escape(title_raw)}</h1><p>{_html.escape(desc_raw)}</p><p>This guide explores key architectural steps to establish air-gapped sovereign operations.</p>"

    canonical = f"https://aiautomatedsystems.ca/blog/{clean_slug}"
    article_schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title_raw,
        "description": desc_raw,
        "mainEntityOfPage": canonical,
        "publisher": {"@type": "Organization", "name": "AI Automated Systems"},
    })

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_html.escape(title_raw)} — AI Automated Systems</title>
<meta name='description' content='{_html.escape(desc_raw)}'>
<link rel='canonical' href='{canonical}'>
<script type='application/ld+json'>{article_schema}</script>
<style>
:root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:760px;margin:5vh auto;padding:0 20px;line-height:1.7}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:2.5rem;box-shadow:0 12px 30px rgba(31,41,51,.06)}}
h1{{font-size:2.2rem;line-height:1.2;margin-bottom:1rem}}
h2{{font-size:1.5rem;margin-top:2rem;margin-bottom:.5rem}}
p,li{{color:var(--text);margin:.6rem 0}}
a{{color:var(--accent);text-decoration:none}}
</style></head>
<body>
<p><a href='/blog'>← All Posts</a></p>
<div class='card'>
{body}
<hr style='border:0;border-top:1px solid var(--border);margin:2rem 0'>
<p><a style='font-weight:700' href='/pricing'>Explore Our Sovereign AI Software Packages →</a></p>
</div>
</body></html>"""
    return HTMLResponse(html)


@router.get("/blog/rss.xml", response_class=Response)
async def blog_rss():
    """Generate valid RSS 2.0 feed."""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Local AI Ops Blog — AI Automated Systems</title>
    <link>https://aiautomatedsystems.ca/blog</link>
    <description>Practical, field-tested guides for running private AI.</description>
    <item>
      <title>Running Sovereign Local LLMs: Zero-Telemetry Production Stack</title>
      <link>https://aiautomatedsystems.ca/blog/local-inference-guide</link>
      <description>How to deploy air-gapped LLM endpoints with deterministic latency.</description>
    </item>
  </channel>
</rss>"""
    return Response(xml, media_type="application/rss+xml")
