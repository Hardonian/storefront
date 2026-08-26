"""Customer demand sensing and intent gap intelligence from support inquiries and lead notes."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger("storefront.demand")

INTENT_RULES: list[tuple[str, str, list[str]]] = [
    (
        "hipaa_compliance",
        "Healthcare & HIPAA",
        ["hipaa", "phi", "medical", "patient", "clinical", "redaction", "ehr", "emr"],
    ),
    (
        "gpu_compute",
        "GPU Capacity & Hardware",
        ["gpu", "v100", "p40", "vram", "h100", "a100", "cuda", "rent", "compute", "epyc"],
    ),
    (
        "local_llm",
        "Private LLM & Local Inference",
        ["ollama", "vllm", "llama", "mistral", "deepseek", "gguf", "inference", "quantization"],
    ),
    (
        "diffusion_workflows",
        "Creative & Diffusion",
        ["comfyui", "flux", "sdxl", "diffusion", "lora", "workflow", "rendering"],
    ),
    (
        "automation",
        "Sovereign Automation",
        ["n8n", "zapier", "make", "webhook", "postgres", "cron", "docker-compose"],
    ),
    (
        "enterprise_governance",
        "Governance & Retainers",
        ["audit", "soc2", "legal", "compliance", "sentinel", "retainer", "governance"],
    ),
]


def extract_intent_tags(text: str) -> list[str]:
    """Identify matching intent categories from input text."""
    lowered = text.lower()
    tags = []
    for tag_id, _, keywords in INTENT_RULES:
        if any(re.search(rf"\b{re.escape(k)}\b", lowered) for k in keywords):
            tags.append(tag_id)
    return tags or ["general_inquiry"]


def record_demand_signal(query: str, source: str = "api_ask", db_path: str | None = None) -> int:
    """Record an anonymized customer inquiry and extract product intent signals."""
    if not query or len(query.strip()) < 3:
        return 0

    tags = extract_intent_tags(query)
    primary_category = tags[0] if tags else "unclassified"
    tags_str = ",".join(tags)

    target_db = db_path or settings.db_path
    try:
        with get_db(target_db) as conn:
            cursor = conn.execute(
                """INSERT INTO demand_signals (category, raw_query, intent_tags, source)
                   VALUES (?, ?, ?, ?)""",
                (primary_category, query[:500], tags_str, source),
            )
            return cursor.lastrowid or 0
    except Exception as e:
        logger.warning("Failed to record demand signal: %s", e)
        return 0


def get_demand_insights(db_path: str | None = None) -> dict[str, Any]:
    """Aggregate customer inquiry topics to highlight product opportunities."""
    target_db = db_path or settings.db_path
    try:
        with get_db(target_db) as conn:
            rows = conn.execute(
                """SELECT category, COUNT(*) as c
                   FROM demand_signals
                   GROUP BY category
                   ORDER BY c DESC"""
            ).fetchall()

            total = sum(r["c"] for r in rows)
            breakdown = [{"category": r["category"], "count": r["c"], "share_pct": round(r["c"] / max(1, total) * 100, 1)} for r in rows]

            recent = conn.execute(
                "SELECT category, raw_query, detected_at FROM demand_signals ORDER BY id DESC LIMIT 15"
            ).fetchall()

            return {
                "total_signals": total,
                "breakdown": breakdown,
                "recent_signals": [
                    {"category": r["category"], "query": r["raw_query"], "detected_at": r["detected_at"]}
                    for r in recent
                ],
            }
    except Exception as e:
        logger.warning("Failed to load demand insights from %s: %s", target_db, e)
        return {"total_signals": 0, "breakdown": [], "recent_signals": []}
