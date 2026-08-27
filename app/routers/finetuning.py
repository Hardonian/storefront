"""Fine-Tuning & Model Optimization API and Interactive UI."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.core.config import public_brand
from app.services.finetuning_service import (
    BASE_MODEL_SPECS,
    GPU_HARDWARE_SPECS,
    estimate_finetuning_vram,
    estimate_training_duration_and_cost,
    generate_unsloth_recipe,
)

router = APIRouter(tags=["Fine-Tuning & Model Optimization"])


class FineTuneEstimateRequest(BaseModel):
    model_key: str = Field(default="llama-3.1-8b", max_length=64)
    method: str = Field(default="qlora_4bit", max_length=32)
    lora_r: int = Field(default=16, ge=4, le=128)
    context_length: int = Field(default=4096, ge=1024, le=65536)
    batch_size: int = Field(default=2, ge=1, le=32)
    gradient_accumulation_steps: int = Field(default=4, ge=1, le=64)
    dataset_tokens: int = Field(default=5_000_000, ge=10_000, le=1_000_000_000)
    epochs: int = Field(default=3, ge=1, le=20)
    hardware_key: str = Field(default="tesla_v100_32", max_length=64)


class RecipeExportRequest(BaseModel):
    model_key: str = Field(default="llama-3.1-8b", max_length=64)
    lora_r: int = Field(default=16, ge=4, le=128)
    lora_alpha: int = Field(default=32, ge=8, le=256)
    context_length: int = Field(default=4096, ge=1024, le=65536)
    learning_rate: float = Field(default=2e-4, gt=0, le=1e-2)
    epochs: int = Field(default=3, ge=1, le=20)


@router.post("/api/tools/finetune-estimate")
async def api_finetune_estimate(payload: FineTuneEstimateRequest):
    """Calculate exact fine-tuning VRAM memory, training duration, and cost."""
    vram_calc = estimate_finetuning_vram(
        model_key=payload.model_key,
        method=payload.method,
        lora_r=payload.lora_r,
        context_length=payload.context_length,
        batch_size=payload.batch_size,
        gradient_accumulation_steps=payload.gradient_accumulation_steps,
    )

    training_calc = estimate_training_duration_and_cost(
        model_key=payload.model_key,
        dataset_tokens=payload.dataset_tokens,
        epochs=payload.epochs,
        hardware_key=payload.hardware_key,
        method=payload.method,
    )

    return {
        "status": "ok",
        "vram_breakdown": vram_calc,
        "training_estimate": training_calc,
        "supported_models": list(BASE_MODEL_SPECS.keys()),
        "available_hardware": list(GPU_HARDWARE_SPECS.keys()),
    }


@router.post("/api/tools/finetune-recipe-export")
async def api_recipe_export(payload: RecipeExportRequest):
    """Generate production-ready, air-gapped Unsloth / TRL fine-tuning script."""
    script = generate_unsloth_recipe(
        model_key=payload.model_key,
        lora_r=payload.lora_r,
        lora_alpha=payload.lora_alpha,
        context_length=payload.context_length,
        learning_rate=payload.learning_rate,
        epochs=payload.epochs,
    )
    return PlainTextResponse(script, media_type="text/x-python")


@router.get("/tools/finetuning-optimizer", response_class=HTMLResponse)
async def finetuning_optimizer_page(request: Request):
    """Interactive Sovereign Fine-Tuning & LoRA Optimizer Surface."""
    site_base, site_name = public_brand(request)
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Sovereign Fine-Tuning & LoRA Optimizer — {site_name}</title>
  <meta name="description" content="Calculate exact VRAM memory budgets, training hours, and export air-gapped LoRA/QLoRA recipes for Llama-3, DeepSeek, and Mistral.">
  <link rel="canonical" href="{site_base}/tools/finetuning-optimizer">
  <style>
    :root {{
      --bg: #0b1120;
      --card: #131d33;
      --card-alt: #1a2744;
      --accent: #0f766e;
      --accent-glow: #14b8a6;
      --amber: #f59e0b;
      --green: #10b981;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --border: #233354;
      --price: #fbbf24;
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.6;
      padding: 2.5rem 1.5rem;
      min-height: 100vh;
      background-image: radial-gradient(circle at 10% 10%, rgba(20,184,166,.12), transparent 36rem), radial-gradient(circle at 90% 90%, rgba(245,158,11,.08), transparent 32rem);
    }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    .site-nav {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; }}
    .site-nav a {{ color: var(--muted); text-decoration: none; font-weight: 600; font-size: .9rem; }}
    .site-nav a:hover {{ color: var(--accent-glow); }}
    
    .hero-header {{ text-align: center; margin-bottom: 2.5rem; }}
    .eyebrow {{ color: var(--accent-glow); text-transform: uppercase; letter-spacing: .15em; font-size: .75rem; font-weight: 800; }}
    h1 {{ font-size: clamp(2rem, 4vw, 2.8rem); letter-spacing: -.03em; margin: .4rem 0 .6rem; color: #fff; }}
    .hero-sub {{ color: var(--muted); font-size: 1.05rem; max-width: 720px; margin: 0 auto; }}

    .grid-layout {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-top: 1.5rem; }}
    @media (max-width: 860px) {{ .grid-layout {{ grid-template-columns: 1fr; }} }}

    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.75rem;
      box-shadow: 0 12px 30px rgba(0,0,0,.35);
      position: relative;
    }}
    .card h2 {{ font-size: 1.25rem; color: #fff; margin-bottom: 1.2rem; display: flex; align-items: center; gap: .5rem; }}
    
    .form-group {{ margin-bottom: 1.1rem; }}
    label {{ display: block; font-size: .82rem; font-weight: 700; color: var(--muted); margin-bottom: .35rem; text-transform: uppercase; letter-spacing: .05em; }}
    select, input[type=number] {{
      width: 100%;
      padding: .75rem 1rem;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card-alt);
      color: #fff;
      font-size: .95rem;
      outline: none;
      transition: border-color .2s;
    }}
    select:focus, input[type=number]:focus {{ border-color: var(--accent-glow); }}

    .slider-container {{ display: flex; align-items: center; gap: 1rem; }}
    input[type=range] {{ flex: 1; accent-color: var(--accent-glow); cursor: pointer; }}
    .slider-val {{ min-width: 60px; font-weight: 800; color: var(--accent-glow); text-align: right; }}

    .vram-bar-container {{ margin: 1.2rem 0; }}
    .vram-bar-label {{ display: flex; justify-content: space-between; font-size: .85rem; margin-bottom: .4rem; }}
    .vram-bar-track {{ height: 16px; background: var(--card-alt); border-radius: 999px; overflow: hidden; display: flex; }}
    .vram-seg-weights {{ background: #3b82f6; height: 100%; }}
    .vram-seg-trainable {{ background: #10b981; height: 100%; }}
    .vram-seg-act {{ background: #f59e0b; height: 100%; }}
    .vram-seg-over {{ background: #64748b; height: 100%; }}

    .vram-legend {{ display: flex; flex-wrap: wrap; gap: .8rem; font-size: .75rem; color: var(--muted); margin-top: .6rem; }}
    .vram-legend span {{ display: inline-flex; align-items: center; gap: .3rem; }}
    .dot-blue {{ width: 8px; height: 8px; border-radius: 50%; background: #3b82f6; }}
    .dot-green {{ width: 8px; height: 8px; border-radius: 50%; background: #10b981; }}
    .dot-amber {{ width: 8px; height: 8px; border-radius: 50%; background: #f59e0b; }}
    .dot-gray {{ width: 8px; height: 8px; border-radius: 50%; background: #64748b; }}

    .stat-badge-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: .8rem; margin: 1.2rem 0; }}
    .stat-badge {{ background: var(--card-alt); padding: .85rem; border-radius: 10px; border: 1px solid var(--border); }}
    .stat-badge span {{ font-size: .75rem; color: var(--muted); display: block; }}
    .stat-badge b {{ font-size: 1.25rem; color: #fff; }}

    .hw-card {{ background: var(--card-alt); border: 1px solid var(--border); border-radius: 10px; padding: .9rem 1rem; margin-bottom: .6rem; display: flex; justify-content: space-between; align-items: center; }}
    .hw-card.active {{ border-color: var(--accent-glow); box-shadow: 0 0 15px rgba(20,184,166,.2); }}

    .code-preview {{ background: #070b14; border: 1px solid var(--border); border-radius: 8px; padding: 1rem; font-family: monospace; font-size: .8rem; color: #34d399; max-height: 220px; overflow-y: auto; white-space: pre; margin-top: 1rem; }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: .4rem;
      padding: .75rem 1.4rem;
      border-radius: 8px;
      font-weight: 700;
      font-size: .9rem;
      cursor: pointer;
      border: 0;
      text-decoration: none;
      transition: all .2s;
    }}
    .btn-primary {{ background: var(--accent); color: #fff; }}
    .btn-primary:hover {{ background: #115e59; box-shadow: 0 4px 16px rgba(20,184,166,.3); }}
    .btn-secondary {{ background: var(--card-alt); color: #fff; border: 1px solid var(--border); }}
    .btn-secondary:hover {{ border-color: var(--accent-glow); }}

    footer {{ text-align: center; margin-top: 3rem; color: var(--muted); font-size: .85rem; }}
    footer a {{ color: var(--accent-glow); text-decoration: none; }}
  </style>
</head>
<body>
<div class="container">
  <nav class="site-nav">
    <a href="/">← Back to Storefront</a>
    <div>
      <a href="/tools/model-benchmarks">Model Benchmarks</a> · 
      <a href="/tools/hardware-sizer">Hardware Sizer</a> · 
      <a href="/p/hardonia-compute-api-access">Rent GPU Cluster</a>
    </div>
  </nav>

  <header class="hero-header">
    <div class="eyebrow">Autonomous Optimization Layer</div>
    <h1>Sovereign Fine-Tuning & LoRA Optimizer</h1>
    <p class="hero-sub">Parametric VRAM budgeting, dataset token sizing, and air-gapped training script generation for Llama-3, DeepSeek, and Mistral architectures.</p>
  </header>

  <div class="grid-layout">
    <!-- Left Configuration Panel -->
    <div class="card">
      <h2>⚙️ Training Configuration</h2>

      <div class="form-group">
        <label>Base Foundation Model</label>
        <select id="model_select" onchange="recalculate()">
          <option value="llama-3.1-8b" selected>Meta Llama 3.1 8B Instruct</option>
          <option value="llama-3.3-70b">Meta Llama 3.3 70B Instruct (Heavyweight)</option>
          <option value="deepseek-r1-qwen-32b">DeepSeek R1 Distill Qwen 32B (Reasoning)</option>
          <option value="qwen-2.5-coder-32b">Qwen 2.5 Coder 32B (Coding)</option>
          <option value="mistral-nemo-12b">Mistral NeMo 12B (Enterprise)</option>
          <option value="phi-3.5-mini-3.8b">Microsoft Phi 3.5 Mini 3.8B (Edge)</option>
        </select>
      </div>

      <div class="form-group">
        <label>Fine-Tuning Method</label>
        <select id="method_select" onchange="recalculate()">
          <option value="qlora_4bit" selected>4-bit QLoRA (Optimal VRAM & Speed)</option>
          <option value="lora_8bit">8-bit LoRA (High Precision)</option>
          <option value="lora_16bit">16-bit LoRA (Full Precision Adapters)</option>
          <option value="full_16bit">Full Parameter 16-bit (Cluster Required)</option>
        </select>
      </div>

      <div class="form-group">
        <label>LoRA Rank ($r$)</label>
        <div class="slider-container">
          <input type="range" id="lora_r" min="4" max="64" step="4" value="16" oninput="document.getElementById('r_val').textContent = this.value; recalculate();">
          <span class="slider-val" id="r_val">16</span>
        </div>
      </div>

      <div class="form-group">
        <label>Context Window Length (Tokens)</label>
        <select id="context_len" onchange="recalculate()">
          <option value="2048">2,048 Tokens</option>
          <option value="4096" selected>4,096 Tokens</option>
          <option value="8192">8,192 Tokens</option>
          <option value="16384">16,384 Tokens</option>
        </select>
      </div>

      <div class="form-group">
        <label>Dataset Size (Tokens)</label>
        <select id="dataset_size" onchange="recalculate()">
          <option value="1000000">1 Million Tokens (~2,000 examples)</option>
          <option value="5000000" selected>5 Million Tokens (~10,000 examples)</option>
          <option value="20000000">20 Million Tokens (~40,000 examples)</option>
          <option value="100000000">100 Million Tokens (Full Corpus)</option>
        </select>
      </div>

      <div class="form-group">
        <label>Training Epochs</label>
        <div class="slider-container">
          <input type="range" id="epochs" min="1" max="10" value="3" oninput="document.getElementById('ep_val').textContent = this.value; recalculate();">
          <span class="slider-val" id="ep_val">3</span>
        </div>
      </div>
    </div>

    <!-- Right Telemetry & Execution Panel -->
    <div class="card">
      <h2>📊 VRAM & Cost Telemetry</h2>

      <div class="vram-bar-container">
        <div class="vram-bar-label">
          <span>Required GPU VRAM</span>
          <b id="total_vram_display" style="color:var(--accent-glow)">0 GB</b>
        </div>
        <div class="vram-bar-track">
          <div id="seg_weights" class="vram-seg-weights" style="width:50%"></div>
          <div id="seg_trainable" class="vram-seg-trainable" style="width:15%"></div>
          <div id="seg_act" class="vram-seg-act" style="width:20%"></div>
          <div id="seg_over" class="vram-seg-over" style="width:15%"></div>
        </div>
        <div class="vram-legend">
          <span><span class="dot-blue"></span> Weights</span>
          <span><span class="dot-green"></span> Optimizer & LoRA</span>
          <span><span class="dot-amber"></span> Activations</span>
          <span><span class="dot-gray"></span> Workspace</span>
        </div>
      </div>

      <div class="stat-badge-grid">
        <div class="stat-badge">
          <span>Trainable Parameters</span>
          <b id="trainable_pct_display">—</b>
        </div>
        <div class="stat-badge">
          <span>Estimated Duration</span>
          <b id="duration_display">—</b>
        </div>
        <div class="stat-badge">
          <span>Sovereign GPU Cost</span>
          <b id="cost_display" style="color:var(--price)">—</b>
        </div>
        <div class="stat-badge">
          <span>Cloud Savings vs Hyperscaler</span>
          <b id="savings_display" style="color:var(--green)">—</b>
        </div>
      </div>

      <h3 style="font-size:1rem;color:#fff;margin:1.2rem 0 .6rem">Recommended GPU Topology</h3>
      <div id="hardware_list"></div>

      <div style="display:flex;gap:.8rem;margin-top:1.2rem;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="exportRecipe()">Export Unsloth Recipe (.py) ↓</button>
        <a class="btn btn-secondary" href="/p/hardonia-compute-api-access">Deploy on Compute Farm →</a>
      </div>
    </div>
  </div>

  <div class="card" style="margin-top:1.5rem">
    <h2>📜 Generated Air-Gapped Training Recipe</h2>
    <p style="color:var(--muted);font-size:.85rem">Zero-telemetry Python script configured with FlashAttention-2, gradient checkpointing, and 8-bit AdamW.</p>
    <div class="code-preview" id="code_box">Click 'Recalculate' to generate live script preview...</div>
  </div>

  <footer>
    <p>{site_name} · <a href="/tools/hardware-sizer">Hardware Topology Sizer</a> · <a href="/status">System Status</a> · <a href="/contact">Support</a></p>
  </footer>
</div>

<script>
function recalculate() {{
  var model = document.getElementById('model_select').value;
  var method = document.getElementById('method_select').value;
  var r = parseInt(document.getElementById('lora_r').value);
  var ctx = parseInt(document.getElementById('context_len').value);
  var tokens = parseInt(document.getElementById('dataset_size').value);
  var ep = parseInt(document.getElementById('epochs').value);

  fetch('/api/tools/finetune-estimate', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      model_key: model,
      method: method,
      lora_r: r,
      context_length: ctx,
      batch_size: 2,
      gradient_accumulation_steps: 4,
      dataset_tokens: tokens,
      epochs: ep,
      hardware_key: 'tesla_v100_32'
    }})
  }}).then(function(r) {{ return r.json(); }})
  .then(function(d) {{
    var vb = d.vram_breakdown.breakdown_gb;
    var tot = vb.total_vram;
    document.getElementById('total_vram_display').textContent = tot + ' GB';

    // Update VRAM Bar Segments
    var pW = (vb.base_weights / tot) * 100;
    var pT = (vb.trainable_and_optimizer / tot) * 100;
    var pA = (vb.activations / tot) * 100;
    var pO = (vb.cuda_overhead / tot) * 100;

    document.getElementById('seg_weights').style.width = pW + '%';
    document.getElementById('seg_trainable').style.width = pT + '%';
    document.getElementById('seg_act').style.width = pA + '%';
    document.getElementById('seg_over').style.width = pO + '%';

    // Summary stats
    document.getElementById('trainable_pct_display').textContent = d.vram_breakdown.trainable_summary.trainable_percent + '% (' + (d.vram_breakdown.trainable_summary.trainable_parameters / 1e6).toFixed(1) + 'M params)';
    document.getElementById('duration_display').textContent = d.training_estimate.training_hours + ' hrs (' + d.training_estimate.throughput_tokens_sec + ' tok/s)';
    document.getElementById('cost_display').textContent = '$' + d.training_estimate.cost_usd;
    document.getElementById('savings_display').textContent = 'Save $' + d.training_estimate.savings_usd + ' vs Cloud';

    // Hardware recommendations
    var hwHtml = '';
    d.vram_breakdown.recommended_hardware.forEach(function(hw) {{
      hwHtml += '<div class="hw-card ' + (hw.fits ? 'active' : '') + '"><div><b>' + hw.name + '</b><div style="font-size:.78rem;color:var(--muted)">Headroom: ' + hw.headroom_gb + ' GB free</div></div><b style="color:var(--price)">$' + hw.hourly_cost_usd + '/hr</b></div>';
    }});
    document.getElementById('hardware_list').innerHTML = hwHtml;

    // Fetch Recipe Preview
    fetch('/api/tools/finetune-recipe-export', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        model_key: model,
        lora_r: r,
        lora_alpha: r * 2,
        context_length: ctx,
        learning_rate: 0.0002,
        epochs: ep
      }})
    }}).then(function(res) {{ return res.text(); }})
    .then(function(code) {{
      document.getElementById('code_box').textContent = code;
    }});
  }});
}}

function exportRecipe() {{
  var model = document.getElementById('model_select').value;
  var r = parseInt(document.getElementById('lora_r').value);
  var ctx = parseInt(document.getElementById('context_len').value);
  var ep = parseInt(document.getElementById('epochs').value);

  fetch('/api/tools/finetune-recipe-export', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{
      model_key: model,
      lora_r: r,
      lora_alpha: r * 2,
      context_length: ctx,
      learning_rate: 0.0002,
      epochs: ep
    }})
  }}).then(function(res) {{ return res.text(); }})
  .then(function(code) {{
    var blob = new Blob([code], {{type: 'text/x-python'}});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'train_' + model + '_lora.py';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }});
}}

window.addEventListener('DOMContentLoaded', recalculate);
</script>
</body>
</html>"""
    return HTMLResponse(html)
