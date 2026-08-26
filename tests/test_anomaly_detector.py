"""Tests for autonomous platform anomaly detector."""

import sqlite3

from app.core.database import init_analytics_database, init_database
from app.funnel_truth import CLASS_UNKNOWN, init_funnel_schema, record_funnel_event
from app.services.anomaly_detector import get_active_anomalies, inspect_funnel_health, record_anomaly


def test_record_and_fetch_anomalies(tmp_path):
    db = tmp_path / "anomalies.db"
    init_database(db)

    anomaly_id = record_anomaly(
        anomaly_type="5xx_spike",
        severity="warning",
        description="High 500 error rate on checkout redirect",
        metadata={"count": 15},
        db_path=str(db),
    )
    assert anomaly_id > 0

    active = get_active_anomalies(db_path=str(db))
    assert len(active) == 1
    assert active[0]["type"] == "5xx_spike"
    assert active[0]["metadata"]["count"] == 15


def test_funnel_checkout_stall_detection(tmp_path):
    db = tmp_path / "funnel_anomalies.db"
    init_database(db)
    init_funnel_schema(db)

    # 25 checkout starts with 0 payments -> triggers stall anomaly
    for i in range(25):
        record_funnel_event(
            db,
            stage="checkout_start",
            classification=CLASS_UNKNOWN,
            classification_reason="test",
        )

    diag = inspect_funnel_health(db_path=str(db))
    assert diag["status"] == "degraded"
    assert diag["drop_detected"] is True
    assert len(diag["anomalies"]) > 0
