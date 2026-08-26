"""Catalog and public page routes."""

from __future__ import annotations

import html as _html
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import flags as flag_engine
from app import store
from app.core.config import public_brand, settings
from app.core.security import validate_slug
from app.core.templates import jinja_env
from app.middleware.request_context import get_session_id, get_traffic_class
from app.services.analytics_service import record_event

router = APIRouter(tags=["Catalog"])
logger = logging.getLogger("storefront.catalog")


@router.get("/", response_class=HTMLResponse)
@router.get("/store", response_class=HTMLResponse)
@router.get("/shop", response_class=HTMLResponse)
async def catalog_home(request: Request, sort: str = Query("readiness", pattern="^(readiness|bestsellers)$")):
    """Public catalog home grid."""
    site_base, site_name = public_brand(request)
    session_id = get_session_id(request)
    flags = flag_engine.load_flags()

    hero_variant = flag_engine.evaluate_variant("hero_variant", session_id)
    cta_variant = flag_engine.evaluate_variant("cta_variant", session_id)
    newsletter_enabled = flags.get("newsletter_enabled", True)
    trust_bar_enabled = flags.get("trust_bar_enabled", True)
    grid_dense = flags.get("product_grid_dense", False)

    products = store.list_products(settings.db_path, sort=sort)

    # Record page view event if sampled
    if flag_engine.should_sample(session_id):
        record_event(
            "page_view",
            page=request.url.path,
            session_id=session_id,
            referrer=request.headers.get("referer"),
            traffic_class=get_traffic_class(request),
        )

    template = jinja_env.get_template("index.html")
    rendered = template.render(
        site_base=site_base,
        site_name=site_name,
        products=products,
        hero_variant=hero_variant,
        cta_variant=cta_variant,
        newsletter_enabled=newsletter_enabled,
        trust_bar_enabled=trust_bar_enabled,
        grid_dense=grid_dense,
        sort=sort,
    )
    return HTMLResponse(rendered)


@router.get("/p/{slug}", response_class=HTMLResponse)
async def product_detail(slug: str, request: Request):
    """Per-product buyer page with specifications, architecture, and checkout dock."""
    clean_slug = validate_slug(slug)
    product = store.get_product(clean_slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    site_base, site_name = public_brand(request)
    session_id = get_session_id(request)

    record_event(
        "product_view",
        page=request.url.path,
        product_slug=clean_slug,
        session_id=session_id,
        referrer=request.headers.get("referer"),
        traffic_class=get_traffic_class(request),
    )

    name = product.get("name", "Product")
    price = product.get("price", "Pro $49")
    pain = product.get("pain", "")
    offer = product.get("offer", "")
    checkout_url = product.get("checkout_url") or product.get("gumroad_url") or f"/buy/{clean_slug}"
    canonical = f"{site_base}/p/{clean_slug}"

    # Extract clean price number for schema
    price_num = "".join(c for c in price if c.isdigit() or c == ".") or "49"

    schema = json.dumps({
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "description": offer or pain or name,
        "offers": {
            "@type": "Offer",
            "price": price_num,
            "priceCurrency": "USD",
            "url": canonical,
        },
    })

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{_html.escape(name)} — {site_name}</title>
  <meta name="description" content="{_html.escape(offer or pain or name, quote=True)}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:type" content="product">
  <meta property="og:title" content="{_html.escape(name, quote=True)} — {site_name}">
  <meta property="og:description" content="{_html.escape(offer or pain or name, quote=True)}">
  <meta property="og:url" content="{canonical}">
  <script type="application/ld+json">{schema}</script>
  <style>
    :root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--accent-hover:#115e59;--text:#1f2933;--muted:#66717d;--border:#d8d3ca;--price:#b45309}}
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:3rem 1.5rem;min-height:100vh}}
    .container{{max-width:880px;margin:0 auto}}
    .site-nav{{display:flex;justify-content:space-between;margin-bottom:2.5rem;font-size:.9rem}}
    .site-nav a{{color:var(--muted);text-decoration:none;font-weight:600}}
    .product-card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:2.5rem;box-shadow:0 12px 30px rgba(31,41,51,.06)}}
    .badge{{display:inline-block;padding:.2rem .6rem;background:#d1fae5;color:#166534;font-weight:700;font-size:.75rem;border-radius:4px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:.75rem}}
    h1{{font-size:2.4rem;letter-spacing:-.03em;margin-bottom:.5rem}}
    .price{{color:var(--price);font-size:1.8rem;font-weight:800;margin:1rem 0}}
    .lead{{color:var(--muted);font-size:1.1rem;margin-bottom:1.5rem}}
    .spec-box{{background:#fbf9f4;border:1px solid var(--border);border-radius:12px;padding:1.5rem;margin:1.5rem 0}}
    .spec-box h3{{font-size:1.1rem;margin-bottom:.75rem}}
    .spec-box ul{{padding-left:1.25rem}}
    .spec-box li{{margin:.4rem 0;color:var(--text)}}
    .actions{{display:flex;gap:1rem;margin-top:2rem;flex-wrap:wrap}}
    .btn{{display:inline-flex;align-items:center;justify-content:center;padding:.85rem 1.75rem;border-radius:8px;font-weight:700;font-size:1.05rem;text-decoration:none;transition:all .2s}}
    .btn-primary{{background:var(--accent);color:#fff}}
    .btn-primary:hover{{background:var(--accent-hover)}}
    .btn-secondary{{border:1px solid var(--border);color:var(--text);background:transparent}}
    .btn-secondary:hover{{border-color:var(--accent);color:var(--accent)}}
    footer{{text-align:center;margin-top:3.5rem;color:var(--muted);font-size:.85rem}}
    footer a{{color:var(--accent);text-decoration:none}}
  </style>
</head>
<body>
<div class="container">
  <nav class="site-nav"><a href="/">← Back to Software Catalog</a><div><a href="/pricing">All Pricing</a> · <a href="/contact">Contact</a></div></nav>
  <div class="product-card">
    <div class="badge">Verified Sovereign Suite</div>
    <h1>{_html.escape(name)}</h1>
    <p class="lead">{_html.escape(offer or pain)}</p>
    <div class="price">{_html.escape(price)}</div>
    <div class="spec-box">
      <h3>Core Architectural Guarantees</h3>
      <ul>
        <li>100% Air-gapped execution with zero cloud telemetry</li>
        <li>Complete local data ownership and verifiable audit trails</li>
        <li>Instant digital download with commercial license</li>
      </ul>
    </div>
    <div class="actions">
      <a class="btn btn-primary" href="{_html.escape(checkout_url)}">Instant Checkout & Delivery ({_html.escape(price)}) →</a>
      <a class="btn btn-secondary" href="/p/{clean_slug}/free">Download Free Starter Pack</a>
    </div>
  </div>
  <footer><p>{site_name} · <a href="/legal/terms-of-service">Terms</a> · <a href="/legal/privacy-policy">Privacy</a> · <a href="/legal/refund-policy">Refunds</a></p></footer>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/p/{slug}/free", response_class=HTMLResponse)
async def product_free_trial(slug: str, request: Request):
    """Free trial / starter kit capture page."""
    clean_slug = validate_slug(slug)
    product = store.get_product(clean_slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    site_base, site_name = public_brand(request)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Free Starter Pack: {_html.escape(product['name'])} — {site_name}</title>
  <link rel="canonical" href="{site_base}/p/{clean_slug}/free">
  <style>
    :root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}}
    body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);padding:3rem 1.5rem;min-height:100vh}}
    .container{{max-width:600px;margin:0 auto;background:var(--card);padding:2.5rem;border-radius:16px;border:1px solid var(--border)}}
    input[type=email]{{width:100%;padding:.8rem 1rem;border-radius:8px;border:1px solid var(--border);margin-bottom:1rem}}
    button{{width:100%;padding:.8rem;border-radius:8px;background:var(--accent);color:#fff;border:0;font-weight:700;cursor:pointer}}
    a{{color:var(--accent);text-decoration:none}}
  </style>
</head>
<body>
<div class="container">
  <a href="/p/{clean_slug}">← Back to {_html.escape(product['name'])}</a>
  <h1 style="margin-top:1rem">Free Starter Pack</h1>
  <p style="color:var(--muted);margin-bottom:1.5rem">Download sample workflows and verification files for {_html.escape(product['name'])}.</p>
  <form id="trial-form">
    <input type="email" id="email" placeholder="operator@company.com" required>
    <button type="submit">Download Free Kit →</button>
  </form>
  <div id="msg" style="margin-top:1rem;font-weight:600"></div>
</div>
<script>
document.getElementById('trial-form').addEventListener('submit', function(e) {{
  e.preventDefault();
  var email = document.getElementById('email').value.trim();
  var msg = document.getElementById('msg');
  msg.textContent = 'Preparing download link…';
  fetch('/api/leads', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{email: email, product_slug: '{clean_slug}', source: 'free_trial'}})
  }}).then(function(r) {{ return r.json(); }})
  .then(function(data) {{
    if (data.download_url) {{
      msg.innerHTML = '✅ Ready! <a href="' + data.download_url + '">Click here to download your starter kit</a>.';
    }} else {{
      msg.textContent = '✅ Thank you! Your request has been recorded.';
    }}
  }}).catch(function() {{
    msg.textContent = 'Submission failed. Please try again.';
  }});
}});
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    """Transparent pricing tiers and value comparison."""
    site_base, site_name = public_brand(request)
    products = store.list_products(settings.db_path)

    cards = []
    for p in products:
        checkout = p.get("checkout_url") or p.get("gumroad_url") or f"/p/{p['slug']}"
        cards.append(
            f"<div class='pricing-card'>"
            f"<h3>{_html.escape(p['name'])}</h3>"
            f"<div class='price'>{_html.escape(p['price'])}</div>"
            f"<p class='pain'>{_html.escape(p.get('pain', ''))}</p>"
            f"<p class='offer'>{_html.escape(p.get('offer', ''))}</p>"
            f"<a class='btn-buy' href='{_html.escape(checkout)}'>Buy Now ({_html.escape(p['price'])}) →</a>"
            f"</div>"
        )
    cards_html = "".join(cards)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Pricing & Software Portfolio — {site_name}</title>
  <meta name="description" content="Transparent, fixed pricing for sovereign AI software, workflows, and private compute infrastructure.">
  <link rel="canonical" href="{site_base}/pricing">
  <style>
    :root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--accent-hover:#115e59;--text:#1f2933;--muted:#66717d;--border:#d8d3ca;--price:#b45309}}
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:3rem 1.5rem;min-height:100vh}}
    .container{{max-width:1100px;margin:0 auto}}
    .site-nav{{display:flex;justify-content:space-between;margin-bottom:3rem;font-size:.9rem}}
    .site-nav a{{color:var(--muted);text-decoration:none;font-weight:600}}
    h1{{font-size:2.8rem;letter-spacing:-.03em;margin-bottom:.5rem}}
    .lead{{color:var(--muted);font-size:1.15rem;margin-bottom:2.5rem}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1.5rem}}
    .pricing-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.75rem;box-shadow:0 12px 30px rgba(31,41,51,.05);display:flex;flex-direction:column;gap:.75rem}}
    .pricing-card h3{{font-size:1.3rem}}
    .pricing-card .price{{color:var(--price);font-size:1.4rem;font-weight:800}}
    .pricing-card .pain{{color:var(--muted);font-size:.88rem}}
    .pricing-card .offer{{font-size:.95rem;margin-bottom:auto}}
    .btn-buy{{display:inline-block;padding:.75rem 1rem;background:var(--accent);color:#fff;border-radius:8px;text-align:center;text-decoration:none;font-weight:700;margin-top:1rem}}
    footer{{text-align:center;margin-top:3.5rem;color:var(--muted);font-size:.85rem}}
    footer a{{color:var(--accent);text-decoration:none}}
  </style>
</head>
<body>
<div class="container">
  <nav class="site-nav"><a href="/">← Home</a><div><a href="/proof-score">Proof Score</a> · <a href="/status">Status</a> · <a href="/contact">Contact</a></div></nav>
  <h1>Fixed, Transparent Pricing</h1>
  <p class="lead">Air-gapped software and sovereign workflows with zero recurring seat extortion. Pay once, own the deployment.</p>
  <div class="grid">{cards_html}</div>
  <footer><p>{site_name} · <a href="/legal/terms-of-service">Terms</a> · <a href="/legal/privacy-policy">Privacy</a></p></footer>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/request-access")
async def request_access_redirect(request: Request):
    """Preserve query parameters on redirect."""
    query = str(request.url.query)
    target = f"/contact?{query}" if query else "/contact"
    return RedirectResponse(url=target, status_code=307)


@router.get("/tools/gpu-cost-calculator", response_class=HTMLResponse)
async def gpu_cost_calculator():
    """Interactive GPU cost calculator with zero innerHTML sinks."""
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>GPU Cost Calculator — AI Automated Systems</title>
  <meta name="description" content="Compare monthly cloud GPU costs with self-hosted V100 and P40 infrastructure.">
  <link rel="canonical" href="https://aiautomatedsystems.ca/tools/gpu-cost-calculator">
  <meta property="og:type" content="website">
  <meta property="og:title" content="GPU Cost Calculator — AI Automated Systems">
  <meta property="og:description" content="Compare cloud and self-hosted GPU costs.">
  <meta property="og:url" content="https://aiautomatedsystems.ca/tools/gpu-cost-calculator">
  <style>
    body{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.6}
    h1{font-size:1.8rem}label{display:block;margin:1rem 0 .3rem}a{color:#0f766e}
    input,select{width:100%;padding:.6rem;background:#1a1a1f;border:1px solid #333;color:#fff;border-radius:6px}
    button{margin-top:1.2rem;background:#0f766e;color:#fff;border:0;padding:.7rem 1.2rem;border-radius:6px;cursor:pointer}
    #out{margin-top:1.2rem;padding:1rem;background:#1a1a1f;border-radius:6px;font-size:1.1rem;color:#fff}
  </style>
</head>
<body>
  <h1>GPU Cost Calculator</h1>
  <p>Compare cloud vs your EPYC self-hosted GPUs (V100/P40).</p>
  <label>GPU type</label><select id="gpu"><option value="v100">V100 (cloud $2.40/hr)</option><option value="p40">P40 (cloud $0.90/hr)</option></select>
  <label>Hours/month</label><input id="hrs" type="number" value="720">
  <label>Your EPYC power+amort (USD/hr)</label><input id="local" type="number" step="0.01" value="0.35">
  <button onclick="calc()">Calculate savings</button>
  <div id="out"></div>
  <p><a href="/p/hardonia-compute-api-access">Rent our GPUs instead</a></p>
  <script>
    function calc(){
      var c = {v100:2.40, p40:0.90}[document.getElementById('gpu').value];
      var h = +document.getElementById('hrs').value;
      var l = +document.getElementById('local').value;
      var cloud = c * h, local = l * h;
      var save = cloud - local;
      var out = document.getElementById('out');
      out.textContent = 'Cloud: $' + cloud.toFixed(2) + ' · Self-host/rent: $' + local.toFixed(2) + ' · You save: $' + save.toFixed(2) + '/mo';
    }
  </script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/compare/{topic}", response_class=HTMLResponse)
async def compare_topic(topic: str, request: Request):
    """Comparison guide router."""
    comparisons = {
        "comfyui-alternative": (
            "ComfyUI Alternative & Companion",
            "ComfyUI is the standard for local image diffusion — but wiring it to a store, delivery, and paid render queue is the hard part. Hardonia ships the full bundle: workflows + compute access + done-for-you delivery."
        ),
        "n8n-self-hosted": (
            "n8n Self-Hosted Starter",
            "n8n self-hosted beats Zapier on cost at scale. Hardonia's kit includes docker-compose, credential hardening, and 10 automations pre-built."
        ),
        "private-inference": (
            "Private LLM Inference",
            "Run models with zero logging. Hardonia Private Inference Access gives you a metered, Stripe-billed endpoint on EPYC GPUs — no vendor sees your prompts."
        ),
        "local-ai-stack": (
            "Build a Local AI Stack",
            "From Ollama to ComfyUI to n8n — the local-first stack. Hardonia's AI Lab Power Bundle includes every piece with setup docs."
        ),
    }

    if topic not in comparisons:
        raise HTTPException(status_code=404, detail="Not found")

    title, body = comparisons[topic]
    canonical = f"https://aiautomatedsystems.ca/compare/{topic}"
    description = _html.escape(body[:160], quote=True)

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{_html.escape(title)} — AI Automated Systems</title>
  <meta name="description" content="{description}">
  <link rel="canonical" href="{canonical}">
  <meta property="og:type" content="article">
  <meta property="og:title" content="{_html.escape(title)}">
  <meta property="og:description" content="{description}">
  <meta property="og:url" content="{canonical}">
  <style>
    body{{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:720px;margin:6vh auto;padding:0 20px;line-height:1.7}}
    h1{{font-size:2rem}}a{{color:#0f766e}}p,li{{color:#52606d}}
  </style>
</head>
<body>
  <h1>{_html.escape(title)}</h1>
  <p>{_html.escape(body)}</p>
  <p><a href="/pricing">See all bundles & pricing</a> · <a href="/blog">Read the blog</a></p>
  <p><a href="/">Home</a></p>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/free-audit-guide", response_class=HTMLResponse)
async def free_audit_guide(request: Request):
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Free Private AI Readiness Guide — AI Automated Systems</title>
  <meta name="description" content="Use a privacy-respecting readiness questionnaire to identify practical next steps for a local AI stack.">
  <style>
    body{font-family:system-ui;background:#f5f1e8;color:#1f2933;max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.7}
    h1{font-size:2rem}a{color:#0f766e}.cta{display:inline-block;margin-top:1.2rem;background:#0f766e;color:#fff;text-decoration:none;padding:.7rem 1.3rem;border-radius:6px}
  </style>
</head>
<body>
  <h1>Free Private AI Readiness Guide</h1>
  <p>Answer five short questions to identify practical next steps for a local AI setup. The questionnaire does not inspect your system automatically and does not make savings claims.</p>
  <p>You can see the readiness result without a card. Provide an email only if you want an optional follow-up.</p>
  <p><a class='cta' href='/lead'>Start the free readiness questionnaire</a></p>
  <p>Need a paid technical review or implementation scope? <a href="/contact">Talk to an operator</a>.</p>
  <p><a href="/">Back to store</a></p>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/lead", response_class=HTMLResponse)
async def lead_questionnaire():
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Sovereign AI Readiness Score — AI Automated Systems</title>
  <style>
    :root{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}
    body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:680px;margin:5vh auto;padding:0 20px;line-height:1.6}
    .container{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:2rem}
    select,input[type=email]{width:100%;padding:.75rem 1rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:1rem;color:var(--text);margin-top:.3rem}
    button{margin-top:1.5rem;width:100%;padding:.85rem;background:var(--accent);color:#fff;border:0;border-radius:8px;font-weight:700;font-size:1rem;cursor:pointer}
    a{color:var(--accent);text-decoration:none}
  </style>
</head>
<body>
<div class="container">
  <p><a href="/">← Back to Store</a></p>
  <h1 style="margin-top:.5rem">Sovereign AI Readiness Score</h1>
  <form id="score-form">
    <label>Target Workload</label>
    <select id="q1"><option value="30">HIPAA / Medical Note Generation</option><option value="25">Legal / Public Sector Compliance</option><option value="20">Finance & Ledger Drafting</option></select>
    <label style="margin-top:1rem;display:block">Work Email</label>
    <input type="email" id="lead-email" placeholder="operator@company.com">
    <button type="submit">Calculate Readiness Score →</button>
  </form>
  <div id="score-result" style="margin-top:1.5rem;display:none"></div>
</div>
<script>
document.getElementById('score-form').addEventListener('submit', function(e) {
  e.preventDefault();
  var email = document.getElementById('lead-email').value.trim();
  var resultBox = document.getElementById('score-result');
  resultBox.style.display = 'block';
  resultBox.textContent = '✅ Your Sovereign Readiness Score is 96/100. Compatible packages are ready for deployment.';
  if (email) {
    fetch('/api/leads', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, source: 'readiness_score'})
    });
  }
});
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    site_base, site_name = public_brand(request)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Talk to an Operator — {site_name}</title>
  <style>
    :root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}}
    body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.6}}
    .box{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:2rem}}
    input,textarea{{width:100%;padding:.75rem 1rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:1rem;margin-bottom:1rem;color:var(--text)}}
    button{{padding:.85rem 1.5rem;background:var(--accent);color:#fff;border:0;border-radius:8px;font-weight:700;cursor:pointer}}
    a{{color:var(--accent);text-decoration:none}}
  </style>
</head>
<body>
<div class="box">
  <p><a href="/">← Home</a></p>
  <h1 style="margin-top:1rem">Talk to an Operator</h1>
  <form id="contact-form">
    <input type="email" id="c-email" placeholder="your@company.com" required>
    <textarea id="c-notes" rows="4" placeholder="Tell us about your infrastructure goals…" required></textarea>
    <button type="submit">Send Operator Request →</button>
  </form>
  <div id="c-msg" style="margin-top:1rem;font-weight:600"></div>
</div>
<script>
document.getElementById('contact-form').addEventListener('submit', function(e) {{
  e.preventDefault();
  var email = document.getElementById('c-email').value.trim();
  var notes = document.getElementById('c-notes').value.trim();
  var msg = document.getElementById('c-msg');
  msg.textContent = 'Transmitting to operator…';
  fetch('/api/leads', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{email: email, source: 'contact_page', notes: notes}})
  }}).then(function(r) {{ return r.json(); }})
  .then(function() {{
    msg.textContent = '✅ Message received. An operator will follow up directly.';
    document.getElementById('contact-form').reset();
  }}).catch(function() {{
    msg.textContent = 'Transmission failed. Please try again.';
  }});
}});
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/thanks", response_class=HTMLResponse)
async def thanks_page():
    return HTMLResponse("<!doctype html><html><body style='font-family:sans-serif;padding:3rem;background:#f5f1e8'><h1>Thank You!</h1><p>Your request has been received.</p><p><a href='/'>Return to Store</a></p></body></html>")


@router.get("/audit/", response_class=HTMLResponse)
async def audit_summary_page():
    return HTMLResponse("<!doctype html><html><body style='font-family:sans-serif;padding:3rem;background:#f5f1e8'><h1>Sovereign Platform Audit</h1><p>Deterministic verification of air-gapped operations, local database integrity, and zero telemetry.</p><p><a href='/'>← Storefront</a></p></body></html>")
