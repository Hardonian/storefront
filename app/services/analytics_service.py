"""Analytics and telemetry recording service."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger("storefront.analytics")


def record_event(
    event: str,
    page: str | None = None,
    product_slug: str | None = None,
    checkout_url: str | None = None,
    session_id: str | None = None,
    referrer: str | None = None,
    traffic_class: str = "unclassified",
    src: str | None = None,
    db_path: str | Path | None = None,
    **kwargs: Any,
) -> None:
    """Record an analytics event to SQLite with resilient error handling."""
    data = {
        "page": page,
        "checkout_url": checkout_url,
        "session_id": session_id,
        "referrer": referrer,
        "traffic_class": traffic_class,
        "src": src,
    }
    data.update(kwargs)
    payload = json.dumps(data, separators=(",", ":"))

    target_db = db_path or settings.effective_analytics_db_path
    try:
        with get_db(target_db) as conn:
            conn.execute(
                "INSERT INTO events (product_slug, event_type, source, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (product_slug, event, "storefront", payload),
            )
    except Exception as e:
        logger.warning("Failed to record analytics event '%s' in %s: %s", event, target_db, e)


def get_analytics_summary(db_path: str | Path | None = None) -> dict[str, Any]:
    """Return aggregated event counts and recent events for operator inspection."""
    target_db = db_path or settings.effective_analytics_db_path
    try:
        with get_db(target_db) as conn:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) as c FROM events GROUP BY event_type ORDER BY c DESC"
            ).fetchall()
            recent = conn.execute(
                "SELECT product_slug, event_type, created_at FROM events "
                "ORDER BY created_at DESC LIMIT 50"
            ).fetchall()

            return {
                "totals": {r[0]: r[1] for r in rows},
                "recent": [
                    {
                        "product_slug": r[0],
                        "event_type": r[1],
                        "created_at": r[2],
                    }
                    for r in recent
                ],
            }
    except Exception as e:
        logger.warning("Error loading analytics summary from %s: %s", target_db, e)
        return {"totals": {}, "recent": []}
