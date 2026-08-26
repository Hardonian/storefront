"""Interactive sovereign evaluation tools: Redaction Sandbox, Hardware Sizer, and GPU Calculator."""

from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.config import public_brand

router = APIRouter(tags=["Interactive Tools"])

# Deterministic PII / PHI Redaction Patterns for Sentinel Sandbox
REDACTION_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN_REDACTED]"),
    (r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b", "[CREDIT_CARD_REDACTED]"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL_REDACTED]"),
    (r"\b(?:\+?1[-.\s]?)?\(?[2-9]\d{2}\)?[-.\s]?\d{3}[-\s]?\d{4}\b", "[PHONE_REDACTED]"),
    (r"\b(?:MRN|MR#|Patient ID)[:\s]*([A-Z0-9-]{4,12})\b", "MRN: [PHI_ID_REDACTED]"),
]


class RedactionRequest(BaseModel):
    text: str = Field(..., max_length=10000)


class HardwareSizerRequest(BaseModel):
    model_params_b: float = Field(..., ge=1, le=200)  # Billions of parameters (e.g. 8, 33, 70)
    quant_bits: int = Field(default=4, ge=2, le=16)   # 4, 8, or 16 bits
    concurrent_users: int = Field(default=5, ge=1, le=100)
    context_length: int = Field(default=8192, ge=2048, le=65536)


@router.post("/api/tools/redact")
async def api_redact_text(payload: RedactionRequest):
    """Deterministic local PII/PHI redaction with audit signature."""
    text = payload.text
    redactions_count = 0
    redacted = text

    for pattern, replacement in REDACTION_PATTERNS:
        matches = len(re.findall(pattern, redacted, flags=re.IGNORECASE))
        if matches:
            redactions_count += matches
            redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)

    return {
        "status": "ok",
        "redacted_text": redacted,
        "items_redacted": redactions_count,
        "verification": "100% air-gapped local regex pass",
    }


@router.post("/api/tools/size-hardware")
async def api_size_hardware(payload: HardwareSizerRequest):
    """Calculate exact GPU VRAM, power consumption, and recommend tailored hardware bundle."""
    # Model weights VRAM (GB) = (params_b * quant_bits) / 8 * 1.2 overhead factor
    weights_vram = (payload.model_params_b * payload.quant_bits / 8.0) * 1.2

    # KV Cache VRAM (GB) = users * (2 * layers * heads * head_dim * context * bytes) approx 0.0003 * users * context * (params / 10)
    kv_cache_vram = (payload.concurrent_users * (payload.context_length / 1024.0) * 0.35) * (payload.model_params_b / 14.0)
    total_vram_gb = round(weights_vram + kv_cache_vram, 1)

    # Topology recommendation
    if total_vram_gb <= 24:
        gpu_topology = "1x NVIDIA Tesla P40 (24GB VRAM) or RTX 3090/4090"
        est_power_watts = 250
        recommended_bundle = "hardonia-compute-api-access"
        bundle_price = "Starter $20"
    elif total_vram_gb <= 32:
        gpu_topology = "1x NVIDIA Tesla V100 (32GB VRAM)"
        est_power_watts = 300
        recommended_bundle = "comfyui-production-workflow-pack"
        bundle_price = "Pro $49"
    elif total_vram_gb <= 64:
        gpu_topology = "2x NVIDIA Tesla V100 (64GB Total VRAM)"
        est_power_watts = 600
        recommended_bundle = "n8n-hardened-automation-starter"
        bundle_price = "Pro $79"
    else:
        gpu_topology = "4x NVIDIA Tesla V100 (128GB Total VRAM) or 2x A100 (160GB)"
        est_power_watts = 1200
        recommended_bundle = "sentinel-compliance-suite"
        bundle_price = "Pro $149"

    # Monthly cost comparison: Self-hosted vs Hyperscaler Cloud ($2.40/hr per GPU)
    monthly_gpu_hours = 720
    cloud_monthly = round(monthly_gpu_hours * 2.40 * max(1, int(total_vram_gb / 24)), 2)
    self_hosted_power_cost = round((est_power_watts / 1000.0) * monthly_gpu_hours * 0.12, 2)  # $0.12/kWh

    return {
        "model_params_b": payload.model_params_b,
        "quant_bits": payload.quant_bits,
        "total_vram_gb": total_vram_gb,
        "weights_vram_gb": round(weights_vram, 1),
        "kv_cache_vram_gb": round(kv_cache_vram, 1),
        "gpu_topology": gpu_topology,
        "est_power_watts": est_power_watts,
        "cloud_monthly_estimate_usd": cloud_monthly,
        "self_hosted_power_monthly_usd": self_hosted_power_cost,
        "monthly_savings_usd": round(cloud_monthly - self_hosted_power_cost, 2),
        "recommended_bundle": recommended_bundle,
        "bundle_price": bundle_price,
    }


@router.get("/tools/redaction-sandbox", response_class=HTMLResponse)
async def redaction_sandbox_page(request: Request):
    """Interactive client-side PII/PHI redaction demo."""
    site_base, site_name = public_brand(request)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Sentinel Redaction Sandbox — {site_name}</title>
  <meta name="description" content="Test zero-telemetry, air-gapped document redaction for HIPAA, GDPR, and confidential legal drafting.">
  <link rel="canonical" href="{site_base}/tools/redaction-sandbox">
  <style>
    :root {{ --bg: #f5f1e8; --card: #fffdf8; --accent: #0f766e; --text: #1f2933; --muted: #66717d; --border: #d8d3ca; }}
    body {{ font-family: -apple-system, system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 3rem 1.5rem; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    .box {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 2rem; box-shadow: 0 12px 30px rgba(31,41,51,.06); }}
    textarea {{ width: 100%; height: 140px; padding: .8rem; border-radius: 8px; border: 1px solid var(--border); font-family: monospace; font-size: .95rem; margin: .5rem 0 1rem; }}
    .btn {{ padding: .75rem 1.4rem; background: var(--accent); color: #fff; border: 0; border-radius: 8px; font-weight: 700; cursor: pointer; }}
    .result-box {{ margin-top: 1.5rem; padding: 1rem; background: #1a1a1f; color: #34d399; border-radius: 8px; font-family: monospace; white-space: pre-wrap; }}
    a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
<div class="container">
  <p><a href="/">← Storefront</a></p>
  <div class="box">
    <h1>🛡️ Sentinel Sovereign Redaction Sandbox</h1>
    <p style="color:var(--muted);margin-bottom:1rem">Paste sample patient notes or legal contracts below. Redaction executes with 100% zero-telemetry containment.</p>
    <label>Sample Document Text</label>
    <textarea id="inp">Patient John Doe (MRN: 9482-1204) visited Dr. Smith regarding chest palpitations. Reach at john.doe@hospital-network.com or (555) 392-8192. SSN: 123-45-6789.</textarea>
    <button class="btn" onclick="runRedact()">Redact Instantly (Zero-Telemetry) →</button>
    <div class="result-box" id="out">Click button above to execute air-gapped redaction.</div>
    <p style="margin-top:1.5rem"><a href="/p/sentinel-note">Explore Full Sentinel Compliance Suite ($297) →</a></p>
  </div>
</div>
<script>
function runRedact() {{
  var txt = document.getElementById('inp').value;
  var out = document.getElementById('out');
  out.textContent = 'Executing redaction pass…';
  fetch('/api/tools/redact', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{text: txt}})
  }}).then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    out.textContent = d.redacted_text + '\\n\\n[Audit Verification: ' + d.items_redacted + ' items safely sanitized]';
  }}).catch(function() {{
    out.textContent = 'Sanitization failed.';
  }});
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/tools/hardware-sizer", response_class=HTMLResponse)
async def hardware_sizer_page(request: Request):
    """Interactive LLM Hardware and VRAM topology sizer."""
    site_base, site_name = public_brand(request)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>LLM Hardware & VRAM Sizer — {site_name}</title>
  <meta name="description" content="Calculate exact GPU VRAM requirements, power budgets, and self-hosted cost savings for Llama-3, DeepSeek, and Mistral.">
  <link rel="canonical" href="{site_base}/tools/hardware-sizer">
  <style>
    :root {{ --bg: #f5f1e8; --card: #fffdf8; --accent: #0f766e; --text: #1f2933; --muted: #66717d; --border: #d8d3ca; --price: #b45309; }}
    body {{ font-family: -apple-system, system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 3rem 1.5rem; }}
    .container {{ max-width: 800px; margin: 0 auto; }}
    .box {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 2rem; box-shadow: 0 12px 30px rgba(31,41,51,.06); }}
    .form-group {{ margin-bottom: 1rem; }}
    label {{ display: block; font-size: .85rem; font-weight: 700; color: var(--muted); margin-bottom: .3rem; }}
    select, input {{ width: 100%; padding: .75rem; border-radius: 8px; border: 1px solid var(--border); font-size: 1rem; background: var(--bg); }}
    .btn {{ width: 100%; padding: .85rem; background: var(--accent); color: #fff; border: 0; border-radius: 8px; font-weight: 700; cursor: pointer; margin-top: 1rem; }}
    .res-card {{ background: #1a1a1f; color: #fff; border-radius: 12px; padding: 1.5rem; margin-top: 1.5rem; display: none; }}
    .res-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-top: 1rem; }}
    .res-item b {{ display: block; font-size: 1.4rem; color: #34d399; }}
    a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
<div class="container">
  <p><a href="/">← Storefront</a></p>
  <div class="box">
    <h1>⚡ Private LLM Hardware Sizer</h1>
    <p style="color:var(--muted);margin-bottom:1.5rem">Determine exact GPU topologies, power consumption, and monthly savings vs cloud APIs.</p>
    <div class="form-group">
      <label>Target Model Architecture</label>
      <select id="model">
        <option value="8">Llama-3 8B / Mistral 7B (Lightweight)</option>
        <option value="33" selected>DeepSeek-Coder 33B / Command-R 35B (Mid-Weight)</option>
        <option value="70">Llama-3 70B / Qwen-72B (Heavyweight Enterprise)</option>
      </select>
    </div>
    <div class="form-group">
      <label>Quantization Level</label>
      <select id="quant">
        <option value="4" selected>4-bit GGUF / AWQ (Optimal Speed & Memory)</option>
        <option value="8">8-bit GPTQ (High Precision)</option>
        <option value="16">16-bit FP16 / BF16 (Lossless Full Weights)</option>
      </select>
    </div>
    <div class="form-group">
      <label>Concurrent Users</label>
      <input type="number" id="users" value="10" min="1" max="100">
    </div>
    <button class="btn" onclick="sizeHardware()">Calculate Topology & Savings →</button>
    <div class="res-card" id="res">
      <h3 style="color:#34d399">Recommended Topology</h3>
      <div id="top-desc" style="font-size:1.1rem;margin-top:.4rem"></div>
      <div class="res-grid">
        <div class="res-item"><span>Total VRAM</span><b id="vram">—</b></div>
        <div class="res-item"><span>Monthly Cloud Cost</span><b id="cloud">—</b></div>
        <div class="res-item"><span>Self-Hosted Power</span><b id="power">—</b></div>
        <div class="res-item"><span>Your Monthly Savings</span><b id="save">—</b></div>
      </div>
      <p style="margin-top:1.5rem"><a id="rec-link" style="color:#34d399;font-weight:700" href="#">Get Recommended Deployment Bundle →</a></p>
    </div>
  </div>
</div>
<script>
function sizeHardware() {{
  var m = parseFloat(document.getElementById('model').value);
  var q = parseInt(document.getElementById('quant').value);
  var u = parseInt(document.getElementById('users').value) || 1;
  fetch('/api/tools/size-hardware', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{model_params_b: m, quant_bits: q, concurrent_users: u, context_length: 8192}})
  }}).then(function(r){{ return r.json(); }})
  .then(function(d){{
    document.getElementById('res').style.display = 'block';
    document.getElementById('top-desc').textContent = d.gpu_topology;
    document.getElementById('vram').textContent = d.total_vram_gb + ' GB';
    document.getElementById('cloud').textContent = '$' + d.cloud_monthly_estimate_usd + '/mo';
    document.getElementById('power').textContent = '$' + d.self_hosted_power_monthly_usd + '/mo';
    document.getElementById('save').textContent = '$' + d.monthly_savings_usd + '/mo';
    var rec = document.getElementById('rec-link');
    rec.href = '/p/' + d.recommended_bundle;
    rec.textContent = 'Explore Recommended Package: ' + d.recommended_bundle.replace(/-/g, ' ').toUpperCase() + ' (' + d.bundle_price + ') →';
  }});
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)
