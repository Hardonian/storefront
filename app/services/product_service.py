"""Product domain service: catalog querying, enrichment, and filtering."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger("storefront.products")

# Core product enrichment catalog with deep specs, architecture highlights, and category metadata
PRODUCT_ENRICHMENT: dict[str, dict[str, Any]] = {
    "sentinel-note": {
        "category": "Enterprise Suites",
        "badge": "Clinical Tier",
        "highlights": [
            "Local HIPAA & PIPEDA compliant note generation",
            "Zero cloud telemetry — operates entirely air-gapped",
            "Customizable SOAP, DAP, and BIRP templates",
            "Direct integration with local EHR export targets",
        ],
        "specs": {"Latency": "<450ms local", "Models": "Llama-3-8B / Mistral-7B", "Delivery": "Instant ZIP + Docker compose"},
    },
    "ops-draft": {
        "category": "Enterprise Suites",
        "badge": "Government & Legal",
        "highlights": [
            "Municipal policy and compliance document drafting",
            "Deterministic citation verification engine",
            "Audit trail retention with cryptographic signing",
            "Standardized RFP and municipal resolution workflows",
        ],
        "specs": {"Target": "Public sector & Legal", "Deployment": "On-premise / Bare Metal", "Format": "Markdown + PDF"},
    },
    "ledger-draft": {
        "category": "Enterprise Suites",
        "badge": "Finance Ops",
        "highlights": [
            "Deterministic executive financial briefing automation",
            "Reconciliation anomaly memo generator",
            "Zero cloud data transfer for sensitive balance sheets",
            "Plugs directly into local ledger CSV/SQLite exports",
        ],
        "specs": {"Security": "Air-gapped", "Integrations": "SQLite / CSV / Ledger", "Output": "Signed Executive Briefs"},
    },
    "hr-draft": {
        "category": "Enterprise Suites",
        "badge": "Governance",
        "highlights": [
            "Autonomous employee handbook & policy generator",
            "Jurisdiction-aware labor compliance checks",
            "Standardized job descriptions and grievance responses",
            "Private employee record processing",
        ],
        "specs": {"Compliance": "US/CA Multi-jurisdiction", "Storage": "Local encrypted SQLite", "Setup": "Single binary"},
    },
    "hardonia-enterpriser": {
        "category": "Enterprise Suites",
        "badge": "Complete Bundle",
        "highlights": [
            "Includes all 4 flagship suites: Sentinel, Ops, Ledger, HR",
            "Unified local orchestration console & dashboard",
            "Priority updates & multi-seat commercial license",
            "Save over 40% compared to individual suites",
        ],
        "specs": {"Suites Included": "4 Full Packages", "License": "Commercial Unlimited Local", "Support": "Direct Operator"},
    },
    "comfyui-workflow-pack": {
        "category": "Workflows",
        "badge": "Diffusion Pack",
        "highlights": [
            "Production-tested local diffusion pipelines",
            "Optimized for SDXL, Flux.1, and SD 1.5 checkpoints",
            "Turnkey upscale, inpainting, and product render nodes",
            "Includes commercial license & preset weights mapping",
        ],
        "specs": {"Compatible": "ComfyUI / Automatic1111", "VRAM": "8GB - 24GB", "Updates": "Monthly new workflows"},
    },
    "n8n-automation-kit": {
        "category": "Workflows",
        "badge": "Automation Kit",
        "highlights": [
            "10 pre-built self-hosted business automations",
            "Hardened docker-compose configuration with UFW rules",
            "Webhook-to-database pipelines with dead-letter queue",
            "Zero Zapier or Make.com per-task subscription fees",
        ],
        "specs": {"Platform": "n8n Self-Hosted", "Pipelines": "10 Production Workflows", "Docs": "Step-by-step deploy"},
    },
    "hardonia-compute-api-access": {
        "category": "Sovereign Compute",
        "badge": "Private GPU",
        "highlights": [
            "Private EPYC & V100/P40 GPU inference endpoint",
            "Zero prompt logging or retention policy",
            "OpenAI-compatible API format (drop-in replacement)",
            "Metered, Stripe-billed with transparent flat rates",
        ],
        "specs": {"Uptime": "99.9% Hardware SLA", "Protocols": "OpenAI / LiteLLM", "Hardware": "Enterprise EPYC + GPUs"},
    },
    "private-inference-access": {
        "category": "Sovereign Compute",
        "badge": "Zero Logging",
        "highlights": [
            "Fast token generation with guaranteed prompt discard",
            "Ideal for confidential research, legal, and medical AI",
            "Fixed low hourly cost vs hyper-scaler markups",
            "Direct WireGuard or HTTPS loopback access",
        ],
        "specs": {"Privacy": "Guaranteed 0-log memory", "Rate": "Low fixed /hr", "Billing": "Metered Stripe"},
    },
    "private-ai-assurance-retainer": {
        "category": "Retainers & Audits",
        "badge": "Managed SLA",
        "highlights": [
            "Dedicated monthly fractional AI operations engineering",
            "24/7 autonomous watchdog and self-healing telemetry",
            "Quarterly security, performance, and prompt regression audit",
            "Guaranteed 4-hour response time on critical incidents",
        ],
        "specs": {"Capacity": "Limited 5 clients/mo", "Review": "Monthly deep dive", "Delivery": "Continuous ops"},
    },
    "repo-rescue-saas-audit": {
        "category": "Retainers & Audits",
        "badge": "Deep Audit",
        "highlights": [
            "Comprehensive full-stack codebase & infrastructure audit",
            "Identification of secret leaks, security holes, and bottlenecks",
            "Prioritized architectural remediation roadmap",
            "100% credited toward future implementation retainer",
        ],
        "specs": {"Turnaround": "72 Business Hours", "Report": "Executive + Technical PDF", "Guarantee": "Actionable fixes"},
    },
    "ai-change-ledger": {
        "category": "Workflows",
        "badge": "Governance",
        "highlights": [
            "Git-backed prompt and model change management",
            "Deterministic audit trail for compliance and SOC2 reviews",
            "Automatic diff and regression evaluation hooks",
            "Lightweight CLI tool with zero external dependencies",
        ],
        "specs": {"Type": "CLI + Git Hooks", "Storage": "Local Git / SQLite", "Format": "Standardized Changelogs"},
    },
    "ai-lab-health-report": {
        "category": "Audits & Diagnostics",
        "badge": "Health Check",
        "highlights": [
            "Comprehensive benchmark script for local AI infrastructure",
            "Tests GPU compute, VRAM throughput, and latency bounds",
            "Outputs actionable performance optimization report",
            "Includes recommendations for Ollama, vLLM, and ComfyUI",
        ],
        "specs": {"Runtime": "Python 3.10+", "Output": "Markdown + HTML report", "Execution": "Local in <2 mins"},
    },
    "local-ai-ops-checklist": {
        "category": "Audits & Diagnostics",
        "badge": "Field Guide",
        "highlights": [
            "50-point production readiness checklist for private AI",
            "Covers power, thermals, firewall, storage, and models",
            "Prevents expensive hardware mistakes and downtime",
            "Used by enterprise operators to certify local nodes",
        ],
        "specs": {"Format": "PDF + Interactive Markdown", "Updates": "Lifetime updates", "Pages": "24 pages of ops truth"},
    },
}

FALLBACK_PRODUCTS: list[dict[str, Any]] = [
    {
        "slug": "sentinel-note",
        "name": "Sentinel Note",
        "status": "ready",
        "audience": "Clinicians & Medical Practices",
        "pain": "Cloud EHR scribes leak PHI and cost $99/mo per doctor",
        "offer": "Private, local SOAP note generation with zero cloud telemetry",
        "price": "Pro $297",
        "checkout_url": "https://buy.stripe.com/ci-test-sentinel",
        "gumroad_url": "",
        "image_path": "",
        "landing_path": "",
        "readiness_score": 100,
        "dashboard_url": "",
        "dashboard_features": "",
        "sales_count": 42,
    },
    {
        "slug": "ops-draft",
        "name": "OpsDraft",
        "status": "ready",
        "audience": "Municipalities & Legal Teams",
        "pain": "Drafting municipal resolutions and policies takes days",
        "offer": "Deterministic municipal and legal compliance drafting engine",
        "price": "Pro $197",
        "checkout_url": "https://buy.stripe.com/ci-test-ops",
        "gumroad_url": "",
        "image_path": "",
        "landing_path": "",
        "readiness_score": 100,
        "dashboard_url": "",
        "dashboard_features": "",
        "sales_count": 31,
    },
    {
        "slug": "ledger-draft",
        "name": "LedgerDraft",
        "status": "ready",
        "audience": "Finance & Controllers",
        "pain": "Monthly ledger reconciliation briefing takes hours of manual formatting",
        "offer": "Deterministic financial briefing and audit-trail generation",
        "price": "Pro $197",
        "checkout_url": "https://buy.stripe.com/ci-test-ledger",
        "gumroad_url": "",
        "image_path": "",
        "landing_path": "",
        "readiness_score": 100,
        "dashboard_url": "",
        "dashboard_features": "",
        "sales_count": 28,
    },
    {
        "slug": "hr-draft",
        "name": "HRDraft",
        "status": "ready",
        "audience": "HR & Compliance Officers",
        "pain": "Maintaining policies across changing labor regulations is error-prone",
        "offer": "Autonomous HR policy, handbook & grievance drafting engine",
        "price": "Pro $197",
        "checkout_url": "https://buy.stripe.com/ci-test-hr",
        "gumroad_url": "",
        "image_path": "",
        "landing_path": "",
        "readiness_score": 100,
        "dashboard_url": "",
        "dashboard_features": "",
        "sales_count": 24,
    },
    {
        "slug": "hardonia-enterpriser",
        "name": "Hardonia Enterpriser",
        "status": "ready",
        "audience": "Enterprise Operators & Multi-Department Teams",
        "pain": "Buying disparate AI tools creates security holes and budget drain",
        "offer": "Unified 4-suite bundle: Sentinel, Ops, Ledger, and HR Draft",
        "price": "Enterprise $497",
        "checkout_url": "https://buy.stripe.com/ci-test-enterpriser",
        "gumroad_url": "",
        "image_path": "",
        "landing_path": "",
        "readiness_score": 100,
        "dashboard_url": "",
        "dashboard_features": "",
        "sales_count": 56,
    },
    {
        "slug": "comfyui-workflow-pack",
        "name": "ComfyUI Workflow Pack",
        "status": "ready",
        "audience": "Creators & Studios",
        "pain": "Diffusion node setups break on updates and waste GPU hours",
        "offer": "15 production-grade local diffusion and image workflows",
        "price": "Starter $29",
        "checkout_url": "https://buy.stripe.com/ci-test-comfyui",
        "gumroad_url": "",
        "image_path": "",
        "landing_path": "",
        "readiness_score": 100,
        "dashboard_url": "",
        "dashboard_features": "",
        "sales_count": 89,
    },
    {
        "slug": "n8n-automation-kit",
        "name": "n8n Automation Kit",
        "status": "ready",
        "audience": "Automation Engineers",
        "pain": "Zapier bills balloon to hundreds/mo as business workflows scale",
        "offer": "10 pre-built self-hosted business automations + hardened compose",
        "price": "Starter $39",
        "checkout_url": "https://buy.stripe.com/ci-test-n8n",
        "gumroad_url": "",
        "image_path": "",
        "landing_path": "",
        "readiness_score": 100,
        "dashboard_url": "",
        "dashboard_features": "",
        "sales_count": 72,
    },
    {
        "slug": "hardonia-compute-api-access",
        "name": "Hardonia Compute API Access",
        "status": "ready",
        "audience": "Autonomous Agents & Developers",
        "pain": "Cloud providers inspect prompts and bill steep markup on idle GPUs",
        "offer": "Private EPYC + GPU inference endpoint with zero logging",
        "price": "Usage $20",
        "checkout_url": "https://buy.stripe.com/ci-test-compute",
        "gumroad_url": "",
        "image_path": "",
        "landing_path": "",
        "readiness_score": 100,
        "dashboard_url": "",
        "dashboard_features": "",
        "sales_count": 47,
    },
]


def enrich_product(prod: dict[str, Any]) -> dict[str, Any]:
    """Merge database record with rich UI and domain enrichment."""
    slug = prod.get("slug", "")
    enrichment = PRODUCT_ENRICHMENT.get(slug, {})
    result = dict(prod)
    result["category"] = enrichment.get("category", "Autonomous Workflows")
    result["badge"] = enrichment.get("badge", "Verified")
    result["highlights"] = enrichment.get("highlights", [])
    result["specs"] = enrichment.get("specs", {})
    return result


def public_product(prod: dict[str, Any]) -> dict[str, Any]:
    """Serialize a product dictionary into a clean public representation."""
    enriched = enrich_product(prod)
    return {
        "slug": enriched.get("slug"),
        "name": enriched.get("name"),
        "status": enriched.get("status", "ready"),
        "category": enriched.get("category"),
        "badge": enriched.get("badge"),
        "audience": enriched.get("audience", ""),
        "pain": enriched.get("pain", ""),
        "offer": enriched.get("offer", ""),
        "price": enriched.get("price", ""),
        "checkout_url": enriched.get("checkout_url", ""),
        "gumroad_url": enriched.get("gumroad_url", ""),
        "image_path": enriched.get("image_path", ""),
        "readiness_score": enriched.get("readiness_score", 100),
        "highlights": enriched.get("highlights", []),
        "specs": enriched.get("specs", {}),
    }


def list_products(db_path: Path | str | None = None, sort: str = "readiness") -> list[dict[str, Any]]:
    """List products from database with fallback to curated defaults."""
    target_path = db_path or settings.db_path
    order = "sales_count DESC, name ASC" if sort == "bestsellers" else "readiness_score DESC, name ASC"
    cols = (
        "slug, name, status, audience, pain, offer, price, checkout_url, gumroad_url, "
        "image_path, landing_path, readiness_score, created_at, updated_at, "
        "dashboard_url, dashboard_features, stripe_sku"
    )

    try:
        with get_db(target_path) as conn:
            # Ensure sales_count exists
            try:
                conn.execute("ALTER TABLE products ADD COLUMN sales_count INTEGER NOT NULL DEFAULT 0")
                conn.commit()
            except sqlite3.OperationalError:
                pass

            try:
                rows = conn.execute(f"SELECT {cols}, sales_count FROM products ORDER BY {order}").fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(f"SELECT {cols} FROM products ORDER BY {order}").fetchall()

            if rows:
                return [enrich_product(dict(r)) for r in rows]
    except Exception as e:
        logger.warning("Could not load products from SQLite %s: %s. Using fallback catalog.", target_path, e)

    return [enrich_product(p) for p in FALLBACK_PRODUCTS]


def get_product(slug: str, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """Retrieve a single product by its slug."""
    target_path = db_path or settings.db_path
    try:
        with get_db(target_path) as conn:
            row = conn.execute(
                "SELECT slug, name, status, audience, pain, offer, price, "
                "checkout_url, gumroad_url, image_path, landing_path, "
                "deliverable_path, readiness_score, "
                "dashboard_url, dashboard_features, "
                "created_at, updated_at, stripe_sku FROM products WHERE slug = ?",
                (slug,),
            ).fetchone()
            if row:
                return enrich_product(dict(row))
    except Exception as e:
        logger.warning("Error fetching product %s from %s: %s", slug, target_path, e)

    # Check fallback list
    for p in FALLBACK_PRODUCTS:
        if p["slug"] == slug:
            return enrich_product(p)
    return None
