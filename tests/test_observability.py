"""Tests for the cross-repo observability middleware."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_request_id_echoed_and_minted():
    client = TestClient(app)
    # No inbound header -> minted and echoed
    r = client.get("/health")
    assert r.status_code == 200
    assert "X-Request-ID" in r.headers
    rid = r.headers["X-Request-ID"]
    assert len(rid) >= 16

    # Inbound header reused
    r2 = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r2.headers["X-Request-ID"] == "abc-123"


def test_obs_health_endpoint():
    client = TestClient(app)
    r = client.get("/internal/observability/health")
    assert r.status_code == 200
    body = r.json()
    assert "service" in body and body["service"]
    assert "uptime_seconds" in body
    assert "total_requests" in body


def test_obs_metrics_endpoint():
    client = TestClient(app)
    # Hit a known route a few times to accumulate counters
    for _ in range(3):
        client.get("/health")
    r = client.get("/internal/observability/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["total_requests"] >= 3
    assert "error_rate" in body
    assert "avg_latency_ms" in body


def test_obs_counts_errors():
    client = TestClient(app)
    # Trigger a 404 miss; counters must increment by at least one.
    before = client.get("/internal/observability/metrics").json()["total_requests"]
    client.get("/this-route-should-not-exist-xyz")
    after = client.get("/internal/observability/metrics").json()["total_requests"]
    assert after >= before + 1
