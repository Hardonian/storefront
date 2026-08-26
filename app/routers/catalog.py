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
from app.core.config import public_brand, settings
from app.core.security import validate_slug
from app.core.templates import jinja_env
from app.middleware.request_context import get_session_id, get_traffic_class
from app.services.analytics_service import record_event
from app.services.product_service import get_product, list_products, public_product

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

    # Dynamic feature flag evaluations
    hero_variant = flag_engine.evaluate_variant("hero_variant", session_id)
    cta_variant = flag_engine.evaluate_variant("cta_variant", session_id)
    newsletter_enabled = flags.get("newsletter_enabled", True)
    trust_bar_enabled = flags.get("trust_bar_enabled", True)
    grid_dense = flags.get("product_grid_dense", False)

    products = list_products(settings.db_path, sort=sort)

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
    product = get_product(clean_slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    site_base, site_name = public_brand(request)
    session_id = get_session_id(request)

    # Track product view event
    record_event(
        "product_view",
        page=request.url.path,
        product_slug=clean_slug,
        session_id=session_id,
        referrer=request.headers.get("referer"),
        traffic_class=get_traffic_class(request),
    )

    try:
        template = jinja_env.get_template("product.html")
        return HTMLResponse(
            template.render(
                product=product,
                site_base=site_base,
                site_name=site_name,
            )
        )
    except Exception:
        # Fallback to rich dynamic render if dedicated template is unavailable
        return _render_dynamic_product_page(product, site_base, site_name)


@router.get("/p/{slug}/free", response_class=HTMLResponse)
async def product_free_trial(slug: str, request: Request):
    """Free trial / download capture page for a product."""
    clean_slug = validate_slug(slug)
    product = get_product(clean_slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    site_base, site_name = public_brand(request)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Free Starter Pack: {_html.escape(product['name'])} — {site_name}</title>
<meta name='description' content='Download starter configuration and verification files for {_html.escape(product['name'])}.'>
<link rel='canonical' href='{site_base}/p/{clean_slug}/free'>
<style>
:root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--accent-hover:#115e59;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:3rem 1.5rem;min-height:100vh}}
.container{{max-width:600px;margin:0 auto;background:var(--card);padding:2.5rem;border-radius:16px;border:1px solid var(--border);box-shadow:0 12px 30px rgba(31,41,51,.06)}}
h1{{font-size:1.8rem;margin-bottom:.5rem}}
p{{color:var(--muted);margin-bottom:1.5rem}}
input[type=email]{{width:100%;padding:.8rem 1rem;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text);font-size:1rem;margin-bottom:1rem}}
button{{width:100%;padding:.8rem;border-radius:8px;background:var(--accent);color:#fff;border:0;font-weight:700;font-size:1rem;cursor:pointer;transition:background .2s}}
button:hover{{background:var(--accent-hover)}}
.msg{{margin-top:1rem;font-size:.9rem;min-height:1.2em}}
a{{color:var(--accent);text-decoration:none}}
</style></head><body>
<div class='container'>
<a href='/p/{clean_slug}'>← Back to {_html.escape(product['name'])}</a>
<h1 style='margin-top:1rem'>Free Starter Pack</h1>
<p>Get instant access to sample workflows, configurations, and verification documentation for {_html.escape(product['name'])}.</p>
<form id='trial-form'>
  <input type='email' id='email' placeholder='operator@company.com' required>
  <button type='submit'>Download Free Kit →</button>
</form>
<div id='msg' class='msg'></div>
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
      msg.innerHTML = '✅ Ready! <a href=\"' + data.download_url + '\">Click here to download your starter kit</a>.';
    }} else {{
      msg.textContent = '✅ Thank you! Your starter pack details have been recorded.';
    }}
  }}).catch(function() {{
    msg.textContent = 'Submission failed. Please try again or contact support.';
  }});
}});
</script>
</body></html>"""
    return HTMLResponse(html)


@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    """Transparent pricing tiers and value comparison."""
    site_base, site_name = public_brand(request)
    products = list_products(settings.db_path)

    cards = []
    for p in products:
        cards.append(
            f"<div class='pricing-card'>"
            f"<h3>{_html.escape(p['name'])}</h3>"
            f"<div class='price'>{_html.escape(p['price'])}</div>"
            f"<p class='pain'>{_html.escape(p.get('pain', ''))}</p>"
            f"<p class='offer'>{_html.escape(p.get('offer', ''))}</p>"
            f"<a class='btn-buy' href='/p/{p['slug']}'>View Spec & Buy →</a>"
            f"</div>"
        )
    cards_html = "".join(cards)

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Pricing & Software Portfolio — {site_name}</title>
<meta name='description' content='Transparent, fixed pricing for sovereign AI software, workflows, and private compute infrastructure.'>
<link rel='canonical' href='{site_base}/pricing'>
<style>
:root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--accent-hover:#115e59;--text:#1f2933;--muted:#66717d;--border:#d8d3ca;--price:#b45309}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:3rem 1.5rem;min-height:100vh}}
.container{{max-width:1100px;margin:0 auto}}
.site-nav{{display:flex;justify-content:space-between;align-items:center;gap:1rem;margin-bottom:3rem;font-size:.9rem}}
.site-nav a{{color:var(--muted);text-decoration:none;font-weight:600}}
.site-nav a:hover{{color:var(--text)}}
h1{{font-size:2.8rem;letter-spacing:-.03em;margin-bottom:.5rem}}
.lead{{color:var(--muted);font-size:1.15rem;margin-bottom:2.5rem;max-width:750px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1.5rem}}
.pricing-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:1.75rem;box-shadow:0 12px 30px rgba(31,41,51,.05);display:flex;flex-direction:column;gap:.75rem}}
.pricing-card h3{{font-size:1.3rem}}
.pricing-card .price{{color:var(--price);font-size:1.4rem;font-weight:800}}
.pricing-card .pain{{color:var(--muted);font-size:.88rem}}
.pricing-card .offer{{font-size:.95rem;font-weight:500;margin-bottom:auto}}
.btn-buy{{display:inline-block;padding:.75rem 1rem;background:var(--accent);color:#fff;border-radius:8px;text-align:center;text-decoration:none;font-weight:700;margin-top:1rem;transition:background .2s}}
.btn-buy:hover{{background:var(--accent-hover)}}
footer{{text-align:center;margin-top:3.5rem;color:var(--muted);font-size:.85rem}}
footer a{{color:var(--accent);text-decoration:none}}
</style></head><body>
<div class='container'>
<nav class='site-nav'><a href='/'>← Home</a><div style='display:flex;gap:1rem'><a href='/proof-score'>Proof Score</a><a href='/status'>Status</a><a href='/contact'>Contact</a></div></nav>
<h1>Fixed, Transparent Pricing</h1>
<p class='lead'>Air-gapped software and sovereign workflows with zero recurring seat extortion. Pay once, own the deployment.</p>
<div class='grid'>{cards_html}</div>
<footer><p>{site_name} · <a href='/legal/terms-of-service'>Terms</a> · <a href='/legal/privacy-policy'>Privacy</a></p></footer>
</div>
</body></html>"""
    return HTMLResponse(html)


@router.get("/tools/gpu-cost-calculator", response_class=HTMLResponse)
async def gpu_cost_calculator():
    """Interactive GPU cost calculator comparing cloud vs sovereign self-hosted nodes."""
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>GPU Cost Calculator — AI Automated Systems</title>
<meta name='description' content='Compare monthly cloud GPU costs with sovereign self-hosted V100, P40, and EPYC infrastructure.'>
<link rel='canonical' href='https://aiautomatedsystems.ca/tools/gpu-cost-calculator'>
<style>
:root{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca;--price:#b45309}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:680px;margin:5vh auto;padding:0 20px;line-height:1.6}
h1{font-size:2.2rem;letter-spacing:-.02em}
label{display:block;margin:1.2rem 0 .4rem;font-weight:600;font-size:.9rem}
input,select{width:100%;padding:.75rem 1rem;background:var(--card);border:1px solid var(--border);color:var(--text);border-radius:8px;font-size:1rem}
button{margin-top:1.5rem;background:var(--accent);color:#fff;border:0;padding:.85rem 1.4rem;border-radius:8px;font-size:1rem;font-weight:700;cursor:pointer}
#out{margin-top:1.5rem;padding:1.25rem;background:var(--card);border:1px solid var(--border);border-radius:12px;font-size:1.1rem;font-weight:700;color:var(--price)}
a{color:var(--accent);text-decoration:none}
</style></head>
<body>
<p><a href='/'>← Back to Storefront</a></p>
<h1 style='margin-top:1rem'>GPU Cost & ROI Calculator</h1>
<p style='color:var(--muted)'>Calculate monthly savings by switching from hyperscaler hourly GPU fees to sovereign Hardonia compute endpoints or dedicated local hardware.</p>
<label>Target GPU Type</label>
<select id='gpu'>
  <option value='v100'>NVIDIA V100 32GB (Hyperscaler Cloud ~$2.40/hr)</option>
  <option value='p40'>NVIDIA P40 24GB (Hyperscaler Cloud ~$0.90/hr)</option>
  <option value='a100'>NVIDIA A100 80GB (Hyperscaler Cloud ~$4.20/hr)</option>
</select>
<label>Active GPU Hours / Month</label>
<input id='hrs' type='number' value='720'>
<label>Local / Sovereign Power & Amortized Cost (USD/hr)</label>
<input id='local' type='number' step='0.01' value='0.35'>
<button onclick='calc()'>Calculate Projected Savings →</button>
<div id='out'>Enter your hours above to see your projected monthly savings.</div>
<p style='margin-top:2rem'><a href='/p/hardonia-compute-api-access'>Explore Hardonia Compute API Access ($20 starter) →</a></p>
<script>
function calc() {
  var rates = {v100: 2.40, p40: 0.90, a100: 4.20};
  var gpu = document.getElementById('gpu').value;
  var c = rates[gpu] || 2.40;
  var h = Math.max(1, +document.getElementById('hrs').value);
  var l = +document.getElementById('local').value;
  var cloud = c * h;
  var sovereign = l * h;
  var save = Math.max(0, cloud - sovereign);
  document.getElementById('out').innerHTML =
    'Cloud Cost: <strong>$' + cloud.toFixed(2) + '</strong>/mo<br>' +
    'Sovereign Cost: <strong>$' + sovereign.toFixed(2) + '</strong>/mo<br>' +
    '✨ You save approximately: <strong>$' + save.toFixed(2) + ' / month</strong> ($' + (save*12).toFixed(2) + '/year)';
}
calc();
</script>
</body></html>"""
    return HTMLResponse(html)


@router.get("/compare/{topic}", response_class=HTMLResponse)
async def compare_topic(topic: str, request: Request):
    """Dynamic competitor and architectural comparison pages."""
    comparisons = {
        "comfyui-alternative": (
            "ComfyUI Production Workflows vs Raw Node Chaos",
            "ComfyUI is the global standard for local diffusion, but fragile node dependencies create constant breakage. Hardonia provides production-frozen workflows, certified checkpoints, and zero-breakage updates."
        ),
        "n8n-self-hosted": (
            "Self-Hosted n8n Automation vs Zapier Cloud Taxes",
            "As automation volumes grow, Zapier per-task pricing explodes. Hardonia's n8n starter kit delivers pre-hardened docker-compose, UFW isolation, and 10 production pipelines ready out of the box."
        ),
        "private-inference": (
            "Private Local Inference vs Cloud API Prompt Inspection",
            "Commercial cloud LLMs inspect and retain prompts for training and policy review. Hardonia Private Inference guarantees absolute zero logging with local air-gapped endpoints."
        ),
        "local-ai-stack": (
            "The Sovereign Local AI Stack Blueprint",
            "From Ollama and vLLM to ComfyUI and n8n — build a fully autonomous local intelligence hub with verified hardware requirements and battle-tested configurations."
        ),
    }

    if topic not in comparisons:
        raise HTTPException(status_code=404, detail="Comparison topic not found")

    title, body = comparisons[topic]
    site_base, site_name = public_brand(request)
    canonical = f"{site_base}/compare/{topic}"

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_html.escape(title)} — {site_name}</title>
<meta name='description' content='{_html.escape(body[:160])}'>
<link rel='canonical' href='{canonical}'>
<style>
:root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--accent-hover:#115e59;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:760px;margin:6vh auto;padding:0 20px;line-height:1.7}}
h1{{font-size:2.2rem;line-height:1.2;letter-spacing:-.02em}}
p{{color:var(--muted);margin:1rem 0}}
a{{color:var(--accent);text-decoration:none;font-weight:600}}
.cta-box{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.5rem;margin:2rem 0}}
</style></head>
<body>
<p><a href='/'>← Home</a> · <a href='/pricing'>Pricing</a></p>
<h1>{_html.escape(title)}</h1>
<p style='font-size:1.15rem;color:var(--text)'>{_html.escape(body)}</p>
<div class='cta-box'>
  <h3>Explore Sovereign AI Solutions</h3>
  <p>Run mission-critical operations with deterministic control and zero cloud telemetry.</p>
  <a style='display:inline-block;padding:.6rem 1.2rem;background:var(--accent);color:#fff;border-radius:8px;text-decoration:none' href='/'>View Software Catalog →</a>
</div>
</body></html>"""
    return HTMLResponse(html)


@router.get("/free-audit-guide", response_class=HTMLResponse)
async def free_audit_guide(request: Request):
    """Truthful entry point: a free questionnaire, not an implied free service."""
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Free Private AI Readiness Guide — AI Automated Systems</title>
<meta name='description' content='Use a privacy-respecting readiness questionnaire to identify practical next steps for a local AI stack.'>
<style>
:root{--bg:#f5f1e8;--accent:#0f766e;--text:#1f2933}
body{font-family:system-ui;background:var(--bg);color:var(--text);max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.7}
h1{font-size:2rem}a{color:var(--accent)}
.cta{display:inline-block;margin-top:1.2rem;background:var(--accent);color:#fff;text-decoration:none;padding:.7rem 1.3rem;border-radius:6px;font-weight:700}
</style></head>
<body><h1>Free Private AI Readiness Guide</h1>
<p>Answer five short questions to identify practical next steps for a local AI setup. The questionnaire does not inspect your system automatically and does not make savings claims.</p>
<p>You can see the readiness result without a card. Provide an email only if you want an optional follow-up.</p>
<p><a class='cta' href='/lead'>Start the free readiness questionnaire →</a></p>
<p style='margin-top:1.5rem'>Need a paid technical review or implementation scope? <a href='/contact'>Talk to an operator</a>.</p>
<p><a href='/'>Back to store</a></p></body></html>"""
    return HTMLResponse(html)


@router.get("/lead", response_class=HTMLResponse)
async def lead_questionnaire():
    """Interactive Sovereign AI Readiness Score assessment."""
    html = """<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Sovereign AI Readiness Score — AI Automated Systems</title>
<style>
:root{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--accent-hover:#115e59;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:680px;margin:5vh auto;padding:0 20px;line-height:1.6}
.container{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:2rem;box-shadow:0 12px 30px rgba(31,41,51,.06)}
h1{font-size:1.8rem;margin-bottom:.5rem}
label{display:block;margin:1rem 0 .3rem;font-weight:600;font-size:.9rem}
select,input[type=email]{width:100%;padding:.75rem 1rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:1rem;color:var(--text)}
button{margin-top:1.5rem;width:100%;padding:.85rem;background:var(--accent);color:#fff;border:0;border-radius:8px;font-weight:700;font-size:1rem;cursor:pointer}
button:hover{background:var(--accent-hover)}
#score-result{margin-top:1.5rem;padding:1.25rem;background:#eef6f3;border:1px solid #99d5cf;border-radius:12px;display:none}
a{color:var(--accent);text-decoration:none}
</style></head>
<body>
<div class='container'>
<p><a href='/'>← Back to Store</a></p>
<h1 style='margin-top:.5rem'>Sovereign AI Readiness Score</h1>
<p style='color:var(--muted)'>Evaluate your organization's capability to run private, air-gapped AI models and autonomous workflow pipelines.</p>
<form id='score-form'>
  <label>1. Primary Compliance & Data Constraint</label>
  <select id='q1'>
    <option value='30'>HIPAA / PIPEDA / Healthcare Confidentiality</option>
    <option value='25'>Financial Audit & Trade Secret Protection</option>
    <option value='20'>Cost Optimization / Avoiding Per-Seat Taxes</option>
    <option value='15'>General Exploration / Prototyping</option>
  </select>

  <label>2. Current Infrastructure Posture</label>
  <select id='q2'>
    <option value='30'>Dedicated On-Premise GPU Server / Bare Metal</option>
    <option value='20'>Private Cloud VPC / Dedicated Hardware</option>
    <option value='10'>Shared Public Cloud SaaS</option>
  </select>

  <label>3. Deployment Urgency</label>
  <select id='q3'>
    <option value='40'>Immediate — Active Migration within 30 Days</option>
    <option value='25'>Planning Phase — Q3/Q4 Roadmap</option>
    <option value='10'>Informational Benchmark Only</option>
  </select>

  <label>Work Email (Optional — for custom readiness report)</label>
  <input type='email' id='lead-email' placeholder='you@organization.com'>

  <button type='submit'>Calculate Readiness Score →</button>
</form>
<div id='score-result'></div>
</div>
<script>
document.getElementById('score-form').addEventListener('submit', function(e) {
  e.preventDefault();
  var s1 = +document.getElementById('q1').value;
  var s2 = +document.getElementById('q2').value;
  var s3 = +document.getElementById('q3').value;
  var score = s1 + s2 + s3;
  var email = document.getElementById('lead-email').value.trim();
  var resultBox = document.getElementById('score-result');
  resultBox.style.display = 'block';
  resultBox.innerHTML = '<h3>Your Sovereign Readiness Index: ' + score + '/100</h3>' +
    '<p>Based on your profile, your workflow is an ideal match for air-gapped local suites (Sentinel Note, OpsDraft, Hardonia Enterpriser).</p>' +
    '<p><a style=\"font-weight:700\" href=\"/pricing\">View Compatible Software Packages →</a></p>';
  if (email) {
    fetch('/api/leads', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email: email, source: 'readiness_score', notes: 'score=' + score})
    });
  }
});
</script>
</body></html>"""
    return HTMLResponse(html)


@router.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    """Operator contact and implementation consulting page."""
    site_base, site_name = public_brand(request)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Talk to an Operator — {site_name}</title>
<style>
:root{{--bg:#f5f1e8;--card:#fffdf8;--accent:#0f766e;--text:#1f2933;--muted:#66717d;--border:#d8d3ca}}
body{{font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);max-width:640px;margin:6vh auto;padding:0 20px;line-height:1.6}}
.box{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:2rem}}
h1{{font-size:2rem;margin-bottom:.5rem}}
input,textarea{{width:100%;padding:.75rem 1rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-size:1rem;margin-bottom:1rem;color:var(--text)}}
button{{padding:.85rem 1.5rem;background:var(--accent);color:#fff;border:0;border-radius:8px;font-weight:700;cursor:pointer}}
a{{color:var(--accent);text-decoration:none}}
</style></head>
<body>
<div class='box'>
<p><a href='/'>← Home</a></p>
<h1 style='margin-top:1rem'>Talk to an Operator</h1>
<p style='color:var(--muted)'>Direct communication with our engineering operators for bespoke air-gapped deployments, custom GPU clusters, and enterprise retainers.</p>
<form id='contact-form'>
  <input type='email' id='c-email' placeholder='your@company.com' required>
  <textarea id='c-notes' rows='4' placeholder='Tell us about your infrastructure goals or custom requirements…' required></textarea>
  <button type='submit'>Send Operator Request →</button>
</form>
<div id='c-msg' style='margin-top:1rem;font-weight:600'></div>
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
    msg.textContent = '✅ Message received. An operator will review and follow up directly.';
    document.getElementById('contact-form').reset();
  }}).catch(function() {{
    msg.textContent = 'Transmission failed. Please try again.';
  }});
}});
</script>
</body></html>"""
    return HTMLResponse(html)


@router.get("/thanks", response_class=HTMLResponse)
async def thanks_page():
    return HTMLResponse("<!doctype html><html><body style='font-family:sans-serif;padding:3rem;text-align:center;background:#f5f1e8'><h1>Thank You!</h1><p>Your request has been received.</p><p><a href='/'>Return to Store</a></p></body></html>")


@router.get("/request-access", include_in_schema=False)
async def request_access_redirect():
    return RedirectResponse(url="/contact", status_code=302)


@router.get("/audit/", response_class=HTMLResponse)
async def audit_summary_page():
    return HTMLResponse("<!doctype html><html><body style='font-family:sans-serif;padding:3rem;background:#f5f1e8'><h1>Sovereign Platform Audit</h1><p>Deterministic verification of air-gapped operations, local database integrity, and zero telemetry.</p><p><a href='/'>← Storefront</a></p></body></html>")


def _render_dynamic_product_page(product: dict[str, Any], site_base: str, site_name: str) -> HTMLResponse:
    """Fallback rich dynamic product page renderer."""
    slug = product.get("slug", "")
    name = product.get("name", "Product")
    price = product.get("price", "$0")
    pain = product.get("pain", "")
    offer = product.get("offer", "")
    audience = product.get("audience", "")
    checkout_url = product.get("checkout_url", "") or f"/buy/{slug}"
    highlights = product.get("highlights", [])

    items_html = "".join(f"<li>{_html.escape(h)}</li>" for h in highlights)

    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_html.escape(name)} — {site_name}</title>
<meta name='description' content='{_html.escape(offer or pain)}'>
<link rel='canonical' href='{site_base}/p/{slug}'>
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
</style></head><body>
<div class='container'>
<nav class='site-nav'><a href='/'>← Back to Software Catalog</a><div><a href='/pricing'>All Pricing</a> · <a href='/contact'>Contact</a></div></nav>
<div class='product-card'>
<div class='badge'>Verified Sovereign Suite</div>
<h1>{_html.escape(name)}</h1>
<p class='lead'>{_html.escape(offer or pain)}</p>
<div class='price'>{_html.escape(price)}</div>
<div class='spec-box'>
  <h3>Core Architectural Guarantees</h3>
  <ul>
    {items_html or '<li>100% Air-gapped execution with zero cloud telemetry</li><li>Complete local data ownership and verifiable audit trails</li><li>Instant digital download with commercial license</li>'}
  </ul>
</div>
<div class='actions'>
  <a class='btn btn-primary' href='{_html.escape(checkout_url)}'>Instant Checkout & Delivery →</a>
  <a class='btn btn-secondary' href='/p/{slug}/free'>Download Free Starter Pack</a>
</div>
</div>
<footer><p>{site_name} · <a href='/legal/terms-of-service'>Terms</a> · <a href='/legal/privacy-policy'>Privacy</a> · <a href='/legal/refund-policy'>Refunds</a></p></footer>
</div>
</body></html>"""
    return HTMLResponse(html)
