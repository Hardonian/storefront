"""Legal document viewer and compliance terms routes."""

from __future__ import annotations

import html as _html
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.config import public_brand, settings
from app.core.security import validate_doc_name
from app.core.templates import jinja_env

router = APIRouter(tags=["Legal"])
logger = logging.getLogger("storefront.legal")


def _markdown_to_simple_html(md_text: str) -> tuple[str, str]:
    """Safely convert simple Markdown text into HTML without external heavy dependencies."""
    lines = md_text.splitlines()
    title = "Legal Terms"
    body_parts = []

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            title = s[2:].strip()
            body_parts.append(f"<h1>{_html.escape(title)}</h1>")
        elif s.startswith("## "):
            body_parts.append(f"<h2>{_html.escape(s[3:].strip())}</h2>")
        elif s.startswith("### "):
            body_parts.append(f"<h3>{_html.escape(s[4:].strip())}</h3>")
        elif s.startswith("- "):
            body_parts.append(f"<li>{_html.escape(s[2:].strip())}</li>")
        else:
            body_parts.append(f"<p>{_html.escape(s)}</p>")

    return title, "\n".join(body_parts)


@router.get("/terms", response_class=HTMLResponse)
@router.get("/refund", response_class=HTMLResponse)
@router.get("/privacy", response_class=HTMLResponse)
@router.get("/legal", response_class=HTMLResponse)
@router.get("/legal/{doc}", response_class=HTMLResponse)
async def legal_document(request: Request, doc: str = "terms-of-service"):
    """Render verified legal markdown policies."""
    # Determine requested document from path or parameter
    path_last = request.url.path.strip("/").split("/")[-1]
    raw_doc = path_last if path_last in ("terms", "refund", "privacy", "legal") else doc
    clean_doc = validate_doc_name(raw_doc)

    site_base, site_name = public_brand(request)
    doc_path = Path(settings.legal_dir) / f"{clean_doc}.md"

    if doc_path.exists():
        try:
            md_content = doc_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Error reading legal file %s: %s", doc_path, e)
            md_content = f"# {clean_doc.replace('-', ' ').title()}\n\nStandard sovereign operating terms apply."
    else:
        # Default fallback text
        md_content = (
            f"# {clean_doc.replace('-', ' ').title()}\n\n"
            "All software delivered by AI Automated Systems is provided for local execution. "
            "We do not collect customer telemetry, inspect prompts, or retain sensitive customer records. "
            "For refund inquiries or compliance requests, contact support@aiautomatedsystems.ca."
        )

    title, body_html = _markdown_to_simple_html(md_content)

    try:
        template = jinja_env.get_template("legal.html")
        return HTMLResponse(
            template.render(
                title=title,
                content=body_html,
                site_base=site_base,
                site_name=site_name,
            )
        )
    except Exception:
        # Fallback render
        html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_html.escape(title)} — {site_name}</title>
<style>
:root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:760px;margin:6vh auto;padding:0 20px;line-height:1.7}}
.box{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:2.5rem;box-shadow:0 12px 30px rgba(31,41,51,.06)}}
h1{{font-size:2rem;margin-bottom:1rem}}
h2{{font-size:1.4rem;margin-top:1.5rem;margin-bottom:.5rem}}
p,li{{color:var(--text);margin:.5rem 0}}
a{{color:var(--accent);text-decoration:none}}
</style></head>
<body>
<p><a href='/'>← Storefront Home</a></p>
<div class='box'>
{body_html}
</div>
</body></html>"""
        return HTMLResponse(html)
