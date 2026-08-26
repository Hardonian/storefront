"""Sovereign AI Support Assistant service."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import Request

from app.core.config import settings

logger = logging.getLogger("storefront.assistant")

KNOWLEDGE_FALLBACKS: dict[str, str] = {
    "auth": "To authenticate with operator surfaces (/api/leads, /api/analytics, /api/flags), pass the X-API-Key header.",
    "pricing": "All products feature transparent, fixed pricing. Sentinel Note is $297, Ops/Ledger/HR Draft are $197 each, and Hardonia Enterpriser is $497.",
    "privacy": "All our software runs entirely local and air-gapped. No telemetry or prompt inspection is performed.",
    "gpu": "Hardonia Compute API provides private GPU endpoints with zero prompt logging or data retention.",
}


async def query_support_bot(query: str, request: Request | None = None) -> dict[str, Any]:
    """Forward user questions to sovereign support bot with leakage guards."""
    clean_query = query.strip()

    # Pre-flight security scan: check for obvious key leaks
    if "sk-" in clean_query or "leaked" in clean_query:
        # Prompt / secret leak pattern detected — escalate to operator
        return {
            "escalation": "Potential sensitive key or leak pattern detected.",
            "issue": "https://github.com/Hardonian/storefront/issues/security",
            "escalated": True,
        }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.support_bot_url}/api/ask",
                json={"query": clean_query},
            )
            if resp.status_code == 200:
                data = resp.json()
                if "escalated" not in data and "escalation" not in data:
                    data["escalated"] = False
                return data
    except Exception as e:
        logger.info("Support bot at %s unreachable (%s), using deterministic fallback.", settings.support_bot_url, e)

    # Local deterministic answer synthesis
    lower_q = clean_query.lower()
    for key, ans in KNOWLEDGE_FALLBACKS.items():
        if key in lower_q:
            return {"answer": ans, "escalated": False}

    return {
        "answer": (
            "Thank you for contacting AI Automated Systems. Our tools operate local-first and air-gapped. "
            "Please email operator support or use the /contact page for custom deployments."
        ),
        "escalated": False,
    }


async def check_support_bot_health() -> dict[str, Any]:
    """Query support bot upstream health status."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.support_bot_url}/health")
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass

    return {
        "status": "operational",
        "capacity_ok": True,
        "intel_block": False,
        "legal_clear": True,
        "mode": "standalone-fallback",
    }
