"""Autonomous platform anomaly, error spike, and funnel conversion drop detector."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger("storefront.anomalies")


def record_anomaly(
    anomaly_type: str,
    severity: str,
    description: str,
    metadata: dict[str, Any] | None = None,
    db_path: str | None = None,
) -> int:
    """Record an operational anomaly event in SQLite."""
    target_db = db_path or settings.db_path
    meta_json = json.dumps(metadata or {}, separators=(",", ":"))
    try:
        with get_db(target_db) as conn:
            cursor = conn.execute(
                """INSERT INTO system_anomalies (anomaly_type, severity, description, metadata_json)
                   VALUES (?, ?, ?, ?)""",
                (anomaly_type, severity, description, meta_json),
            )
            return cursor.lastrowid or 0
    except Exception as e:
        logger.warning("Failed to record anomaly: %s", e)
        return 0


def get_active_anomalies(db_path: str | None = None) -> list[dict[str, Any]]:
    """Retrieve unresolved platform anomalies."""
    target_db = db_path or settings.db_path
    try:
        with get_db(target_db) as conn:
            rows = conn.execute(
                """SELECT id, anomaly_type, severity, description, metadata_json, detected_at
                   FROM system_anomalies
                   WHERE resolved = 0
                   ORDER BY id DESC LIMIT 20"""
            ).fetchall()

            results = []
            for r in rows:
                meta = {}
                try:
                    meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
                except Exception:
                    pass
                results.append({
                    "id": r["id"],
                    "type": r["anomaly_type"],
                    "severity": r["severity"],
                    "description": r["description"],
                    "metadata": meta,
                    "detected_at": r["detected_at"],
                })
            return results
    except Exception as e:
        logger.warning("Failed to fetch anomalies from %s: %s", target_db, e)
        return []


def inspect_funnel_health(db_path: str | None = None) -> dict[str, Any]:
    """Analyze recent events to detect conversion drop-offs or checkout stalls."""
    effective_analytics = db_path or settings.effective_analytics_db_path
    diagnostics = {
        "status": "healthy",
        "drop_detected": False,
        "anomalies": [],
        "checkout_conversion_rate": 1.0,
    }

    try:
        with get_db(effective_analytics) as conn:
            # Check funnel events
            rows = conn.execute(
                "SELECT stage, COUNT(*) as c FROM funnel_events GROUP BY stage"
            ).fetchall()
            counts = {r["stage"]: r["c"] for r in rows}

            checkout_starts = counts.get("checkout_start", 0)
            payments = counts.get("provider_payment", 0)

            # If checkouts exist but conversions are zero past 20 checkout starts, trigger anomaly
            if checkout_starts >= 20 and payments == 0:
                diagnostics["status"] = "degraded"
                diagnostics["drop_detected"] = True
                desc = f"Critical Checkout Stall: {checkout_starts} checkouts initiated with 0 completed payments."
                diagnostics["anomalies"].append(desc)
                record_anomaly("checkout_stall", "critical", desc, metadata={"checkouts": checkout_starts, "payments": payments})
            elif checkout_starts > 0:
                rate = round(payments / checkout_starts, 3)
                diagnostics["checkout_conversion_rate"] = rate

    except Exception as e:
        logger.warning("Funnel health check failed: %s", e)

    return diagnostics
