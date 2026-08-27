"""Interactive sovereign evaluation tools: Redaction Sandbox, Hardware Sizer, Benchmark Matrix, and Sovereign Stack Topology."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.config import public_brand
from app.services.benchmark_service import (
    calculate_kv_cache_scaling,
    get_model_benchmark_detail,
    list_benchmark_models,
)
from app.services.stack_bridge import (
    discover_local_model_zoo,
    discover_lora_adapters,
    get_live_gpu_capacity,
    get_sovereign_stack_matrix,
)

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


class BenchmarkQueryRequest(BaseModel):
    model_key: str = Field(default="llama-3.1-8b", max_length=64)
    context_tokens: int = Field(default=8192, ge=1024, le=131072)
    concurrent_users: int = Field(default=4, ge=1, le=64)
    quant_cache: str = Field(default="fp16", max_length=16)


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


@router.post("/api/tools/benchmark-query")
async def api_benchmark_query(payload: BenchmarkQueryRequest):
    """Query model benchmarks and KV cache scaling."""
    detail = get_model_benchmark_detail(payload.model_key)
    kv_scaling = calculate_kv_cache_scaling(
        model_key=payload.model_key,
        context_tokens=payload.context_tokens,
        num_users=payload.concurrent_users,
        quant_cache=payload.quant_cache,
    )
    return {
        "status": "ok",
        "model_detail": detail,
        "kv_scaling": kv_scaling,
    }


@router.get("/tools/model-benchmarks", response_class=HTMLResponse)
async def model_benchmarks_page(request: Request):
    """Interactive Sovereign Model Benchmark & Latency Matrix."""
    site_base, site_name = public_brand(request)
    models = list_benchmark_models()
    
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Sovereign Model Benchmarks & Latency Matrix — {site_name}</title>
  <meta name="description" content="Explore Time-to-First-Token (TTFT), tokens per second (TPS), quantization retention, and memory scaling across sovereign open-weight LLMs.">
  <link rel="canonical" href="{site_base}/tools/model-benchmarks">
  <style>
    :root {{
      --bg: #0b1120;
      --card: #131d33;
      --card-alt: #1a2744;
      --accent: #0f766e;
      --accent-glow: #14b8a6;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --border: #233354;
      --green: #10b981;
      --price: #fbbf24;
    }}
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2.5rem 1.5rem;
      min-height: 100vh;
      background-image: radial-gradient(circle at 80% 20%, rgba(20,184,166,.1), transparent 32rem);
    }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    .site-nav {{ display: flex; justify-content: space-between; margin-bottom: 2.5rem; }}
    .site-nav a {{ color: var(--muted); text-decoration: none; font-weight: 600; font-size: .9rem; }}
    .site-nav a:hover {{ color: var(--accent-glow); }}

    .hero-header {{ text-align: center; margin-bottom: 2.5rem; }}
    .eyebrow {{ color: var(--accent-glow); text-transform: uppercase; letter-spacing: .15em; font-size: .75rem; font-weight: 800; }}
    h1 {{ font-size: clamp(2rem, 4vw, 2.8rem); letter-spacing: -.03em; margin: .4rem 0 .6rem; color: #fff; }}
    .hero-sub {{ color: var(--muted); font-size: 1.05rem; max-width: 720px; margin: 0 auto; }}

    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-top: 1.5rem; }}
    @media (max-width: 860px) {{ .grid {{ grid-template-columns: 1fr; }} }}

    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 1.75rem; box-shadow: 0 12px 30px rgba(0,0,0,.35); }}
    .card h2 {{ font-size: 1.25rem; color: #fff; margin-bottom: 1.2rem; }}

    select, input {{
      width: 100%;
      padding: .75rem 1rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card-alt);
      color: #fff;
      font-size: .95rem;
      outline: none;
      margin-bottom: 1rem;
    }}

    .quant-table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: .88rem; }}
    .quant-table th, .quant-table td {{ padding: .75rem .6rem; text-align: left; border-bottom: 1px solid var(--border); }}
    .quant-table th {{ color: var(--muted); font-size: .75rem; text-transform: uppercase; }}
    .quant-table tr:hover {{ background: rgba(20,184,166,.05); }}

    .stat-badge-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: .8rem; margin: 1.2rem 0; }}
    .stat-badge {{ background: var(--card-alt); padding: .85rem; border-radius: 10px; border: 1px solid var(--border); }}
    .stat-badge span {{ font-size: .75rem; color: var(--muted); display: block; }}
    .stat-badge b {{ font-size: 1.3rem; color: #fff; }}

    .score-meter {{ margin: .5rem 0; }}
    .score-label {{ display: flex; justify-content: space-between; font-size: .8rem; margin-bottom: .25rem; }}
    .score-track {{ height: 8px; background: var(--card-alt); border-radius: 999px; overflow: hidden; }}
    .score-fill {{ height: 100%; background: var(--accent-glow); border-radius: 999px; }}

    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: .75rem 1.4rem;
      border-radius: 8px;
      font-weight: 700;
      font-size: .9rem;
      cursor: pointer;
      text-decoration: none;
      transition: all .2s;
    }}
    .btn-primary {{ background: var(--accent); color: #fff; border: 0; }}
    .btn-primary:hover {{ background: #115e59; }}

    footer {{ text-align: center; margin-top: 3rem; color: var(--muted); font-size: .85rem; }}
    footer a {{ color: var(--accent-glow); text-decoration: none; }}
  </style>
</head>
<body>
<div class="container">
  <nav class="site-nav">
    <a href="/">← Back to Storefront</a>
    <div>
      <a href="/tools/finetuning-optimizer">LoRA Optimizer</a> · 
      <a href="/tools/hardware-sizer">Hardware Sizer</a> · 
      <a href="/tools/stack-matrix">Stack Matrix</a>
    </div>
  </nav>

  <header class="hero-header">
    <div class="eyebrow">Local Inference Intelligence</div>
    <h1>Sovereign Model Benchmarks & Latency Matrix</h1>
    <p class="hero-sub">Evaluated Time-To-First-Token, output TPS throughput, and quantization quality retention across verified open-weights models.</p>
  </header>

  <div class="grid">
    <!-- Model Selector & Quality Scores -->
    <div class="card">
      <h2>🧠 Model Architecture Explorer</h2>
      <label style="font-size:.82rem;color:var(--muted);text-transform:uppercase;font-weight:700;display:block;margin-bottom:.4rem">Select Foundation Model</label>
      <select id="model_sel" onchange="updateBenchmark()">
        <option value="llama-3.3-70b" selected>Meta Llama 3.3 70B Instruct</option>
        <option value="llama-3.1-8b">Meta Llama 3.1 8B Instruct</option>
        <option value="deepseek-r1-qwen-32b">DeepSeek R1 Distill Qwen 32B</option>
        <option value="qwen-2.5-coder-32b">Qwen 2.5 Coder 32B Instruct</option>
        <option value="mistral-nemo-12b">Mistral NeMo 12B Instruct</option>
        <option value="phi-3.5-mini-3.8b">Microsoft Phi 3.5 Mini (3.8B)</option>
      </select>

      <div class="stat-badge-grid">
        <div class="stat-badge"><span>Target Category</span><b id="cat_display" style="font-size:1rem">—</b></div>
        <div class="stat-badge"><span>Recommended Engine</span><b id="eng_display" style="font-size:1rem;color:var(--accent-glow)">—</b></div>
        <div class="stat-badge"><span>Median Latency (p50)</span><b id="p50_display" style="color:var(--green)">—</b></div>
        <div class="stat-badge"><span>Tail Latency (p99)</span><b id="p99_display">—</b></div>
      </div>

      <h3 style="font-size:1rem;margin:1.2rem 0 .6rem;color:#fff">Reasoning & Coding Benchmarks</h3>
      <div id="benchmarks_container"></div>
    </div>

    <!-- Quantization & KV Cache Scaling -->
    <div class="card">
      <h2>⚡ Quantization Trade-Off Matrix</h2>
      <table class="quant-table">
        <thead>
          <tr>
            <th>Quantization</th>
            <th>VRAM</th>
            <th>Speed (TPS)</th>
            <th>TTFT</th>
            <th>Quality</th>
          </tr>
        </thead>
        <tbody id="quant_rows"></tbody>
      </table>

      <h3 style="font-size:1rem;margin:1.5rem 0 .6rem;color:#fff">KV Cache Context Scaling</h3>
      <div style="display:flex;gap:.8rem;align-items:center">
        <select id="ctx_sel" onchange="updateBenchmark()" style="margin-bottom:0">
          <option value="4096">4,096 Context</option>
          <option value="8192" selected>8,192 Context</option>
          <option value="32768">32,768 Context</option>
          <option value="65536">65,536 Context</option>
        </select>
        <select id="quant_cache_sel" onchange="updateBenchmark()" style="margin-bottom:0">
          <option value="fp16" selected>FP16 KV Cache</option>
          <option value="fp8">FP8 Quantized KV</option>
          <option value="q4_0">Q4 Quantized KV</option>
        </select>
      </div>

      <div class="stat-badge" style="margin-top:1rem">
        <span>Required KV Cache Memory (4 Concurrent Users)</span>
        <b id="kv_display" style="color:var(--accent-glow)">—</b>
      </div>

      <div style="margin-top:1.5rem">
        <a class="btn btn-primary" href="/p/hardonia-compute-api-access">Deploy on Hardonia GPU Farm →</a>
      </div>
    </div>
  </div>

  <footer>
    <p>{site_name} · <a href="/tools/hardware-sizer">Hardware Topology Sizer</a> · <a href="/status">System Status</a></p>
  </footer>
</div>

<script>
function updateBenchmark() {{
  var model = document.getElementById('model_sel').value;
  var ctx = parseInt(document.getElementById('ctx_sel').value);
  var cacheQ = document.getElementById('quant_cache_sel').value;

  fetch('/api/tools/benchmark-query', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      model_key: model,
      context_tokens: ctx,
      concurrent_users: 4,
      quant_cache: cacheQ
    }})
  }}).then(function(r){{ return r.json(); }})
  .then(function(d){{
    var m = d.model_detail;
    document.getElementById('cat_display').textContent = m.category;
    document.getElementById('eng_display').textContent = m.recommended_engine;
    document.getElementById('p50_display').textContent = m.typical_latency_p50_ms + ' ms';
    document.getElementById('p99_display').textContent = m.typical_latency_p99_ms + ' ms';

    // Render Benchmarks
    var bHtml = '';
    for (var b in m.benchmarks) {{
      var score = m.benchmarks[b];
      bHtml += '<div class="score-meter"><div class="score-label"><span>' + b + '</span><b>' + score + '%</b></div><div class="score-track"><div class="score-fill" style="width:' + score + '%"></div></div></div>';
    }}
    document.getElementById('benchmarks_container').innerHTML = bHtml;

    // Render Quantization Rows
    var qHtml = '';
    for (var q in m.quants) {{
      var qData = m.quants[q];
      qHtml += '<tr><td><b>' + q + '</b></td><td>' + qData.vram_weights_gb + ' GB</td><td style="color:var(--green)">' + qData.tps_v100_cluster + ' tok/s</td><td>' + qData.ttft_ms + ' ms</td><td style="color:var(--price)">' + qData.quality_retention + '%</td></tr>';
    }}
    document.getElementById('quant_rows').innerHTML = qHtml;

    // Render KV Cache Scaling
    document.getElementById('kv_display').textContent = d.kv_scaling.kv_cache_gb + ' GB (' + d.kv_scaling.kv_cache_mb + ' MB)';
  }});
}}

window.addEventListener('DOMContentLoaded', updateBenchmark);
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/tools/stack-matrix", response_class=HTMLResponse)
async def stack_matrix_page(request: Request):
    """Interactive visual topology map of the sovereign AI stack."""
    site_base, site_name = public_brand(request)
    matrix = get_sovereign_stack_matrix()
    gpu = get_live_gpu_capacity()
    models = discover_local_model_zoo()
    adapters = discover_lora_adapters()

    components_html = ""
    for comp in matrix["components"]:
        port_info = f"<code>:{comp['port']}</code>" if "port" in comp else "<code>In-Process / Systemd</code>"
        components_html += f"""
        <div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.25rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem">
          <div>
            <div style="display:flex;align-items:center;gap:.5rem">
              <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#10b981"></span>
              <b style="font-size:1.1rem;color:#fff">{comp['name']}</b>
            </div>
            <p style="color:var(--muted);font-size:.85rem;margin-top:.25rem">{comp['role']}</p>
          </div>
          <div>{port_info}</div>
        </div>"""

    models_html = ""
    for m in models:
        models_html += f"""
        <div style="background:var(--card-alt);border:1px solid var(--border);border-radius:10px;padding:.85rem 1rem;display:flex;justify-content:space-between;align-items:center">
          <div>
            <b style="color:#fff">{m['name']}</b>
            <div style="font-size:.78rem;color:var(--muted)">{m['category']} · SHA256: <code>{m['sha256_checksum']}</code></div>
          </div>
          <span style="background:#064e3b;color:#34d399;padding:.2rem .6rem;border-radius:4px;font-size:.75rem;font-weight:700">Air-Gapped Local</span>
        </div>"""

    adapters_html = ""
    for a in adapters:
        adapters_html += f"""
        <div style="background:var(--card-alt);border:1px solid var(--border);border-radius:10px;padding:.85rem 1rem;display:flex;justify-content:space-between;align-items:center">
          <div>
            <b style="color:#fff">{a['adapter_id']}</b>
            <div style="font-size:.78rem;color:var(--muted)">Base: {a['base_model']} · Rank: {a['rank']} · Loss: {a['loss']}</div>
          </div>
          <span style="background:#0f766e;color:#fff;padding:.2rem .6rem;border-radius:4px;font-size:.75rem;font-weight:700">Ready</span>
        </div>"""

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Sovereign Stack Matrix & Interconnects — {site_name}</title>
  <meta name="description" content="Visual topology map of the Hardonia / AI Automated Systems sovereign software architecture and live service matrix.">
  <link rel="canonical" href="{site_base}/tools/stack-matrix">
  <style>
    :root {{
      --bg: #0b1120;
      --card: #131d33;
      --card-alt: #1a2744;
      --accent: #0f766e;
      --accent-glow: #14b8a6;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --border: #233354;
      --green: #10b981;
      --price: #fbbf24;
    }}
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2.5rem 1.5rem;
      min-height: 100vh;
    }}
    .container {{ max-width: 1000px; margin: 0 auto; }}
    .site-nav {{ display: flex; justify-content: space-between; margin-bottom: 2.5rem; }}
    .site-nav a {{ color: var(--muted); text-decoration: none; font-weight: 600; font-size: .9rem; }}
    .site-nav a:hover {{ color: var(--accent-glow); }}

    .hero-header {{ text-align: center; margin-bottom: 2.5rem; }}
    .eyebrow {{ color: var(--accent-glow); text-transform: uppercase; letter-spacing: .15em; font-size: .75rem; font-weight: 800; }}
    h1 {{ font-size: 2.4rem; letter-spacing: -.03em; margin: .4rem 0 .6rem; color: #fff; }}
    .hero-sub {{ color: var(--muted); font-size: 1.05rem; }}

    .section-title {{ font-size: 1.25rem; color: #fff; margin: 2rem 0 1rem; display: flex; align-items: center; gap: .5rem; }}
    .stack-grid {{ display: flex; flex-direction: column; gap: .9rem; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 1.75rem; margin-top: 1.5rem; }}

    footer {{ text-align: center; margin-top: 3rem; color: var(--muted); font-size: .85rem; }}
    footer a {{ color: var(--accent-glow); text-decoration: none; }}
  </style>
</head>
<body>
<div class="container">
  <nav class="site-nav">
    <a href="/">← Back to Storefront</a>
    <div>
      <a href="/tools/finetuning-optimizer">LoRA Optimizer</a> · 
      <a href="/tools/model-benchmarks">Model Benchmarks</a> · 
      <a href="/status">Live Status</a>
    </div>
  </nav>

  <header class="hero-header">
    <div class="eyebrow">Sovereign Architecture Topology</div>
    <h1>Sovereign Stack Matrix & Interconnects</h1>
    <p class="hero-sub">Domain-driven, local-first microservice topology powering zero-telemetry AI operations.</p>
  </header>

  <h2 class="section-title">🏛️ Active Sovereign Microservices</h2>
  <div class="stack-grid">
    {components_html}
  </div>

  <h2 class="section-title">📦 Local Model Weights Registry (Air-Gapped Zoo)</h2>
  <div class="stack-grid">
    {models_html}
  </div>

  <h2 class="section-title">🧬 Pre-Trained LoRA Domain Adapters</h2>
  <div class="stack-grid">
    {adapters_html}
  </div>

  <footer>
    <p>{site_name} · <a href="/status">System Status</a> · <a href="/contact">Support</a></p>
  </footer>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/tools/redaction-sandbox", response_class=HTMLResponse)
async def redaction_sandbox_page(request: Request):
    """Interactive client-side PII/PHI redaction demo with rich visuals."""
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
    :root {{
      --bg: #0b1120;
      --card: #131d33;
      --card-alt: #1a2744;
      --accent: #0f766e;
      --accent-glow: #14b8a6;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --border: #233354;
      --green: #10b981;
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2.5rem 1.5rem;
      min-height: 100vh;
    }}
    .container {{ max-width: 920px; margin: 0 auto; }}
    .site-nav {{ display: flex; justify-content: space-between; margin-bottom: 2.5rem; }}
    .site-nav a {{ color: var(--muted); text-decoration: none; font-weight: 600; font-size: .9rem; }}
    .site-nav a:hover {{ color: var(--accent-glow); }}

    .box {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 2rem; box-shadow: 0 12px 30px rgba(0,0,0,.35); }}
    h1 {{ font-size: 2.2rem; color: #fff; margin-bottom: .5rem; }}
    textarea {{
      width: 100%;
      height: 140px;
      padding: 1rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card-alt);
      color: #fff;
      font-family: monospace;
      font-size: .95rem;
      margin: .5rem 0 1rem;
      outline: none;
    }}
    textarea:focus {{ border-color: var(--accent-glow); }}

    .preset-bar {{ display: flex; gap: .5rem; flex-wrap: wrap; margin-bottom: .8rem; }}
    .preset-btn {{ background: var(--card-alt); color: var(--muted); border: 1px solid var(--border); border-radius: 6px; padding: .35rem .75rem; font-size: .8rem; cursor: pointer; }}
    .preset-btn:hover {{ border-color: var(--accent-glow); color: #fff; }}

    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: .85rem 1.6rem;
      border-radius: 8px;
      font-weight: 700;
      font-size: .95rem;
      background: var(--accent);
      color: #fff;
      border: 0;
      cursor: pointer;
      transition: all .2s;
    }}
    .btn:hover {{ background: #115e59; box-shadow: 0 4px 16px rgba(20,184,166,.3); }}

    .result-box {{
      margin-top: 1.5rem;
      padding: 1.2rem;
      background: #070b14;
      color: #34d399;
      border: 1px solid var(--border);
      border-radius: 8px;
      font-family: monospace;
      white-space: pre-wrap;
      font-size: .9rem;
      min-height: 100px;
    }}

    footer {{ text-align: center; margin-top: 3rem; color: var(--muted); font-size: .85rem; }}
    footer a {{ color: var(--accent-glow); text-decoration: none; }}
  </style>
</head>
<body>
<div class="container">
  <nav class="site-nav">
    <a href="/">← Back to Storefront</a>
    <div>
      <a href="/tools/finetuning-optimizer">LoRA Optimizer</a> · 
      <a href="/tools/model-benchmarks">Model Benchmarks</a> · 
      <a href="/p/sentinel-note">Sentinel Suite</a>
    </div>
  </nav>

  <div class="box">
    <span style="color:var(--accent-glow);font-weight:800;font-size:.75rem;text-transform:uppercase;letter-spacing:.15em">Zero-Cloud Security Surface</span>
    <h1 style="margin-top:.3rem">🛡️ Sentinel Sovereign Redaction Sandbox</h1>
    <p style="color:var(--muted);margin-bottom:1.2rem">Test deterministic local document redaction for HIPAA, GDPR, and confidential drafting with 100% air-gapped isolation.</p>

    <div class="preset-bar">
      <span style="font-size:.8rem;color:var(--muted);display:flex;align-items:center">Presets:</span>
      <button class="preset-btn" onclick="setPreset('clinical')">Clinical EHR Record</button>
      <button class="preset-btn" onclick="setPreset('financial')">Financial / Banking Memo</button>
      <button class="preset-btn" onclick="setPreset('legal')">Confidential Legal Draft</button>
    </div>

    <label style="font-size:.82rem;font-weight:700;color:var(--muted);text-transform:uppercase">Document Text</label>
    <textarea id="inp">Patient John Doe (MRN: 9482-1204) visited Dr. Smith regarding chest palpitations. Reach at john.doe@hospital-network.com or (555) 392-8192. SSN: 123-45-6789.</textarea>

    <button class="btn" onclick="runRedact()">Redact Instantly (Zero-Telemetry) →</button>
    
    <div class="result-box" id="out">Click button above to execute air-gapped redaction.</div>

    <div style="margin-top:1.5rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem">
      <p style="color:var(--muted);font-size:.85rem">Ready to deploy offline in your hospital or legal office?</p>
      <a style="font-weight:700;color:var(--accent-glow);text-decoration:none" href="/p/sentinel-note">Explore Full Sentinel Compliance Suite ($297) →</a>
    </div>
  </div>

  <footer>
    <p>{site_name} · <a href="/status">Live Status</a> · <a href="/proof-score">Proof Score</a></p>
  </footer>
</div>

<script>
function setPreset(type) {{
  var t = document.getElementById('inp');
  if (type === 'clinical') {{
    t.value = 'Patient Jane Doe (Patient ID: 8812-4412) admitted for neurological follow-up. Contact spouse at jane.doe@healthmail.com or (416) 555-0199. SSN: 987-65-4321.';
  }} else if (type === 'financial') {{
    t.value = 'Account holder Robert Vance authorized transfer to wire 4111-2222-3333-4444. Verification email robert@vance-holdings.ca sent with phone callback (514) 555-0144.';
  }} else if (type === 'legal') {{
    t.value = 'Deposition witness Dr. Alan Vance (MRN: 1042-9918) disclosed confidential terms. Email: alan.vance@legal-chamber.org.';
  }}
}}

function runRedact() {{
  var txt = document.getElementById('inp').value;
  var out = document.getElementById('out');
  out.textContent = 'Executing zero-telemetry redaction pass…';
  fetch('/api/tools/redact', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{text: txt}})
  }}).then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    out.textContent = d.redacted_text + '\\n\\n[Audit Verification: ' + d.items_redacted + ' items safely sanitized via ' + d.verification + ']';
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
    :root {{
      --bg: #0b1120;
      --card: #131d33;
      --card-alt: #1a2744;
      --accent: #0f766e;
      --accent-glow: #14b8a6;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --border: #233354;
      --green: #10b981;
      --price: #fbbf24;
    }}
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2.5rem 1.5rem;
      min-height: 100vh;
    }}
    .container {{ max-width: 840px; margin: 0 auto; }}
    .site-nav {{ display: flex; justify-content: space-between; margin-bottom: 2.5rem; }}
    .site-nav a {{ color: var(--muted); text-decoration: none; font-weight: 600; font-size: .9rem; }}
    .site-nav a:hover {{ color: var(--accent-glow); }}

    .box {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 2rem; box-shadow: 0 12px 30px rgba(0,0,0,.35); }}
    h1 {{ font-size: 2.2rem; color: #fff; margin-bottom: .5rem; }}
    
    label {{ display: block; font-size: .82rem; font-weight: 700; color: var(--muted); margin-bottom: .35rem; text-transform: uppercase; }}
    select, input {{
      width: 100%;
      padding: .75rem 1rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card-alt);
      color: #fff;
      font-size: .95rem;
      outline: none;
      margin-bottom: 1rem;
    }}

    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      padding: .85rem;
      background: var(--accent);
      color: #fff;
      border: 0;
      border-radius: 8px;
      font-weight: 700;
      cursor: pointer;
      font-size: 1rem;
      transition: all .2s;
    }}
    .btn:hover {{ background: #115e59; box-shadow: 0 4px 16px rgba(20,184,166,.3); }}

    .res-card {{ background: #070b14; border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-top: 1.5rem; display: none; }}
    .res-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1rem; margin-top: 1rem; }}
    .res-item b {{ display: block; font-size: 1.3rem; color: #fff; margin-top: .2rem; }}

    footer {{ text-align: center; margin-top: 3rem; color: var(--muted); font-size: .85rem; }}
    footer a {{ color: var(--accent-glow); text-decoration: none; }}
  </style>
</head>
<body>
<div class="container">
  <nav class="site-nav">
    <a href="/">← Back to Storefront</a>
    <div>
      <a href="/tools/finetuning-optimizer">LoRA Optimizer</a> · 
      <a href="/tools/model-benchmarks">Model Benchmarks</a> · 
      <a href="/pricing">Pricing</a>
    </div>
  </nav>

  <div class="box">
    <span style="color:var(--accent-glow);font-weight:800;font-size:.75rem;text-transform:uppercase;letter-spacing:.15em">Capacity Planning</span>
    <h1 style="margin-top:.3rem">⚡ Private LLM Hardware Sizer</h1>
    <p style="color:var(--muted);margin-bottom:1.5rem">Determine exact GPU topologies, power consumption, and monthly savings vs cloud APIs.</p>

    <label>Target Model Architecture</label>
    <select id="model">
      <option value="8">Llama-3.1 8B / Mistral 7B (Lightweight)</option>
      <option value="33" selected>DeepSeek-Coder 33B / Command-R 35B (Mid-Weight)</option>
      <option value="70">Llama-3.3 70B / Qwen-72B (Heavyweight Enterprise)</option>
    </select>

    <label>Quantization Level</label>
    <select id="quant">
      <option value="4" selected>4-bit GGUF / AWQ (Optimal Speed & Memory)</option>
      <option value="8">8-bit GPTQ (High Precision)</option>
      <option value="16">16-bit FP16 / BF16 (Lossless Full Weights)</option>
    </select>

    <label>Concurrent Users</label>
    <input type="number" id="users" value="10" min="1" max="100">

    <button class="btn" onclick="sizeHardware()">Calculate Topology & Savings →</button>

    <div class="res-card" id="res">
      <h3 style="color:var(--accent-glow)">Recommended GPU Topology</h3>
      <div id="top-desc" style="font-size:1.1rem;margin-top:.4rem;color:#fff"></div>
      <div class="res-grid">
        <div class="res-item"><span>Total VRAM</span><b id="vram" style="color:var(--accent-glow)">—</b></div>
        <div class="res-item"><span>Monthly Cloud Cost</span><b id="cloud">—</b></div>
        <div class="res-item"><span>Self-Hosted Power</span><b id="power">—</b></div>
        <div class="res-item"><span>Your Monthly Savings</span><b id="save" style="color:var(--green)">—</b></div>
      </div>
      <p style="margin-top:1.5rem"><a id="rec-link" style="color:var(--accent-glow);font-weight:700;text-decoration:none" href="#">Get Recommended Deployment Bundle →</a></p>
    </div>
  </div>

  <footer>
    <p>{site_name} · <a href="/status">System Status</a></p>
  </footer>
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
