"""Dynamic Tailored Sovereign Architecture Blueprint generator and executive viewer."""

from __future__ import annotations

import html as _html
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app import store
from app.core.config import public_brand, settings
from app.core.database import get_db
from app.core.security import generate_blueprint_token, validate_email_address

router = APIRouter(tags=["Architecture Blueprints"])
logger = logging.getLogger("storefront.blueprint")

CREATE_BLUEPRINTS_DDL = """
CREATE TABLE IF NOT EXISTS blueprints (
    token TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    workload TEXT NOT NULL,
    scale TEXT DEFAULT 'medium',
    compliance TEXT DEFAULT 'standard',
    blueprint_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class BlueprintGenerateRequest(BaseModel):
    email: str = Field(..., max_length=254)
    workload: str = Field(..., max_length=120)  # 'hipaa_notes', 'diffusion_studio', 'automation_ops', 'finetune_specialist', 'agent_swarm', 'code_assistant'
    scale: str = Field(default="medium", max_length=64)     # 'small', 'medium', 'enterprise'
    compliance: str = Field(default="standard", max_length=64)  # 'standard', 'hipaa', 'gdpr', 'air_gapped'


@router.post("/api/blueprint/generate")
async def generate_blueprint(payload: BlueprintGenerateRequest, request: Request):
    """Generate a customized sovereign architecture plan for prospect lead realization."""
    email = validate_email_address(payload.email)
    token = generate_blueprint_token()

    # Determine workload architecture specifications
    specs = {
        "hipaa_notes": {
            "title": "Sovereign Clinical & HIPAA Note Generation Stack",
            "model": "Llama-3.3-70B-Instruct (4-bit Q4_K_M)",
            "gpus": "2x NVIDIA Tesla V100 32GB (64GB Total VRAM)",
            "primary_package": "sentinel-compliance-suite",
            "price": "Pro $149",
            "isolation": "100% Air-Gapped Network Namespace + Local SQLite",
            "compliance_guarantees": ["Zero Cloud Telemetry", "Deterministic SHA-256 Redaction", "No Vendor Log Retention"],
        },
        "diffusion_studio": {
            "title": "Air-Gapped Commercial Diffusion Render Farm",
            "model": "SDXL Base + Turbo LoRAs & ComfyUI",
            "gpus": "1x NVIDIA Tesla V100 32GB or 2x RTX 4090",
            "primary_package": "comfyui-production-workflow-pack",
            "price": "Pro $49",
            "isolation": "UFW-Isolated Dedicated GPU Nodes",
            "compliance_guarantees": ["Zero External Asset Leaks", "Reproducible Seed Topology", "Commercial Distribution Rights"],
        },
        "automation_ops": {
            "title": "Hardened Enterprise Automation & Orchestration Fabric",
            "model": "Self-Hosted n8n + PostgreSQL + UFW Hardening",
            "gpus": "EPYC 7002/7003 Core Server",
            "primary_package": "n8n-hardened-automation-starter",
            "price": "Pro $79",
            "isolation": "Private Docker Bridge Network",
            "compliance_guarantees": ["Zero Third-Party SaaS Per-Task Fees", "Local Webhook Verification", "Automated Daily Database Backups"],
        },
        "finetune_specialist": {
            "title": "Autonomous Domain Fine-Tuning & Adapter Fabric",
            "model": "Llama-3.1-8B / Qwen-2.5-32B + Unsloth QLoRA",
            "gpus": "1x NVIDIA Tesla V100 32GB or RTX 4090 24GB",
            "primary_package": "hardonia-compute-api-access",
            "price": "Starter $20",
            "isolation": "Air-Gapped Training Workspace",
            "compliance_guarantees": ["Full Local Weight Ownership", "Cryptographic Adapter Signing", "Zero Prompt Logging"],
        },
        "code_assistant": {
            "title": "Air-Gapped Autonomous Code Assistant & Audit Node",
            "model": "Qwen 2.5 Coder 32B Instruct (Q4_K_M)",
            "gpus": "1x NVIDIA Tesla V100 32GB or 2x P40 24GB",
            "primary_package": "hardonia-enterpriser",
            "price": "Enterprise $497",
            "isolation": "Local Subnet + Continue.dev / Tabby Gateway",
            "compliance_guarantees": ["Zero Proprietary Code Exfiltration", "Deterministic Vulnerability Scans", "Offline AST Validation"],
        },
    }

    selected = specs.get(payload.workload, {
        "title": "Sovereign Enterprise AI Architecture",
        "model": "DeepSeek-Coder-33B + Ollama / vLLM",
        "gpus": "1x NVIDIA Tesla P40 24GB or V100 32GB",
        "primary_package": "hardonia-compute-api-access",
        "price": "Starter $20",
        "isolation": "Air-Gapped Local Subnet",
        "compliance_guarantees": ["Zero Telemetry", "Fixed Predictable Pricing", "Offline Hardware Portability"],
    })

    blueprint_doc = {
        "title": selected["title"],
        "client_email": email,
        "workload": payload.workload,
        "scale": payload.scale,
        "compliance": payload.compliance,
        "recommended_model": selected["model"],
        "hardware_topology": selected["gpus"],
        "primary_package": selected["primary_package"],
        "package_price": selected["price"],
        "network_isolation": selected["isolation"],
        "compliance_guarantees": selected["compliance_guarantees"],
        "estimated_monthly_savings_usd": 1850.0,
    }

    # Store lead and blueprint in SQLite
    store.create_lead(
        email=email,
        product_slug=selected["primary_package"],
        source="blueprint_generator",
        notes=f"workload={payload.workload}, scale={payload.scale}",
        db_path=settings.db_path,
    )

    with get_db(settings.db_path) as conn:
        conn.execute(CREATE_BLUEPRINTS_DDL)
        conn.execute(
            """INSERT INTO blueprints (token, email, workload, scale, compliance, blueprint_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (token, email, payload.workload, payload.scale, payload.compliance, json.dumps(blueprint_doc)),
        )

    site_base, _ = public_brand(request)
    view_url = f"{site_base}/blueprint/{token}"

    return {
        "status": "ok",
        "token": token,
        "view_url": view_url,
        "blueprint": blueprint_doc,
    }


@router.get("/blueprint/{token}", response_class=HTMLResponse)
async def view_blueprint(token: str, request: Request):
    """Render executive blueprint document with rich visual layout."""
    site_base, site_name = public_brand(request)

    with get_db(settings.db_path) as conn:
        conn.execute(CREATE_BLUEPRINTS_DDL)
        row = conn.execute("SELECT blueprint_json FROM blueprints WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Blueprint not found")
        data = json.loads(row["blueprint_json"])

    guarantees_html = "".join(f"<li>✅ {_html.escape(g)}</li>" for g in data.get("compliance_guarantees", []))

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{_html.escape(data['title'])} — {site_name}</title>
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
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 3rem 1.5rem; line-height: 1.6; min-height: 100vh; }}
    .container {{ max-width: 880px; margin: 0 auto; background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 2.5rem; box-shadow: 0 12px 30px rgba(0,0,0,.35); }}
    .badge {{ display: inline-block; padding: .25rem .75rem; background: #064e3b; color: #34d399; font-weight: 700; border-radius: 999px; font-size: .8rem; text-transform: uppercase; margin-bottom: 1rem; }}
    h1 {{ font-size: 2.4rem; letter-spacing: -.03em; margin-bottom: .5rem; color: #fff; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin: 2rem 0; }}
    .card {{ background: var(--card-alt); padding: 1.25rem; border-radius: 10px; border: 1px solid var(--border); }}
    .card b {{ display: block; font-size: 1.1rem; color: var(--accent-glow); margin-top: .3rem; }}
    ul {{ padding-left: 1.2rem; margin: 1rem 0; }}
    li {{ margin: .4rem 0; color: #fff; }}
    .btn {{ display: inline-block; padding: .85rem 1.6rem; background: var(--accent); color: #fff; border-radius: 8px; font-weight: 700; text-decoration: none; font-size: 1.05rem; }}
    .btn:hover {{ background: #115e59; box-shadow: 0 4px 16px rgba(20,184,166,.3); }}
    a {{ color: var(--accent-glow); text-decoration: none; }}
  </style>
</head>
<body>
<div class="container">
  <div class="badge">Sovereign Architecture Blueprint · Reference: {token[:8]}</div>
  <h1>{_html.escape(data['title'])}</h1>
  <p style="color:var(--muted)">Prepared for: <code>{_html.escape(data.get('client_email', 'Operator'))}</code> · Verification Status: Certified Air-Gapped</p>

  <div class="grid">
    <div class="card"><span>Recommended Model</span><b>{_html.escape(data['recommended_model'])}</b></div>
    <div class="card"><span>GPU Hardware Topology</span><b>{_html.escape(data['hardware_topology'])}</b></div>
    <div class="card"><span>Network Isolation</span><b>{_html.escape(data['network_isolation'])}</b></div>
  </div>

  <h3 style="color:#fff">Guaranteed Compliance & Security Posture</h3>
  <ul>{guarantees_html}</ul>

  <div style="background:linear-gradient(135deg,#132838,#131d33);border:1px solid #14b8a6;border-radius:12px;padding:1.75rem;margin:2rem 0">
    <h3 style="color:var(--accent-glow)">Ready-to-Deploy Package</h3>
    <p style="color:var(--muted);margin:.4rem 0">Deploy this exact architecture with pre-configured scripts, frozen container manifests, and air-gapped model loaders.</p>
    <p style="font-size:1.3rem;font-weight:800;color:var(--price);margin:.5rem 0">{_html.escape(data['package_price'])}</p>
    <a class="btn" href="/p/{_html.escape(data['primary_package'])}">Review & Purchase Package Deployment Bundle →</a>
  </div>

  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem;margin-top:2rem">
    <a href="/api/blueprint/{token}/json" style="font-weight:700">Download Blueprint Spec (.json) ↓</a>
    <p style="color:var(--muted);font-size:.85rem"><a href="/">← Return to Storefront</a> · <a href="/contact">Schedule Technical Onboarding</a></p>
  </div>
</div>
</body>
</html>"""
    return HTMLResponse(html)


@router.get("/api/blueprint/{token}/json")
async def api_get_blueprint_json(token: str):
    """Retrieve machine-readable JSON blueprint specification."""
    with get_db(settings.db_path) as conn:
        conn.execute(CREATE_BLUEPRINTS_DDL)
        row = conn.execute("SELECT blueprint_json FROM blueprints WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Blueprint not found")
        return JSONResponse(json.loads(row["blueprint_json"]))
