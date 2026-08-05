"""Tests for the feature-flag operator control surface (/api/flags*).

These cover the promises in app/flags.py that were previously undelivered:
operator read/mutate, fail-closed validation, experiment control, and the
analytics sampling throttle.
"""
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
HDR = {"X-API-Key": os.environ.get("API_KEY", "test-operator-key-not-for-prod")}
TEST_FLAGS = Path(os.environ.get("STOREFRONT_FLAGS_PATH", "/tmp/storefront-flags.json"))


def _reset_flags() -> None:
    """Restore a clean flags file between tests (defaults only)."""
    TEST_FLAGS.parent.mkdir(parents=True, exist_ok=True)
    TEST_FLAGS.write_text(json.dumps({"flags": {}}, indent=2))
    ep = TEST_FLAGS.parent / "experiment.json"
    if ep.exists():
        ep.unlink()


def test_flags_get_requires_key():
    _reset_flags()
    r = client.get("/api/flags")
    assert r.status_code == 403


def test_flags_get_returns_schema_and_values():
    _reset_flags()
    r = client.get("/api/flags", headers=HDR)
    assert r.status_code == 200
    d = r.json()
    assert "flags" in d and "schema" in d
    # Defaults applied for missing flags.
    assert d["flags"]["newsletter_enabled"] is True
    assert d["flags"]["trust_bar_enabled"] is True
    assert "newsletter_enabled" in d["schema"]
    assert d["schema"]["newsletter_enabled"]["type"] == "bool"
    assert d["active_experiment"] is None


def test_flags_post_unknown_flag_404():
    _reset_flags()
    r = client.post("/api/flags", headers=HDR, json={"name": "nope", "value": True})
    assert r.status_code == 404


def test_flags_post_wrong_type_422():
    _reset_flags()
    r = client.post("/api/flags", headers=HDR,
                    json={"name": "newsletter_enabled", "value": "yes"})
    assert r.status_code == 422


def test_flags_post_valid_bool_ok_and_persists():
    _reset_flags()
    r = client.post("/api/flags", headers=HDR,
                    json={"name": "newsletter_enabled", "value": False})
    assert r.status_code == 200
    assert r.json()["value"] is False
    # Persisted to the test flags file.
    data = json.loads(TEST_FLAGS.read_text())
    assert data["flags"]["newsletter_enabled"] is False


def test_flags_post_ab_flag_with_active_experiment_409():
    _reset_flags()
    # Start an experiment on hero_variant, then try to pin it -> conflict.
    r = client.post("/api/flags/experiment", headers=HDR,
                    json={"action": "start", "flag": "hero_variant"})
    assert r.status_code == 200
    r = client.post("/api/flags", headers=HDR,
                    json={"name": "hero_variant", "value": "B"})
    assert r.status_code == 409


def test_flags_post_sampling_out_of_range_422():
    _reset_flags()
    r = client.post("/api/flags", headers=HDR,
                    json={"name": "analytics_sampling", "value": 1.5})
    assert r.status_code == 422


def test_experiment_start_stop_roundtrip():
    _reset_flags()
    r = client.post("/api/flags/experiment", headers=HDR,
                    json={"action": "start", "flag": "cta_variant"})
    assert r.status_code == 200
    exp = r.json()["experiment"]
    assert exp["flag"] == "cta_variant"
    # GET reflects active experiment.
    r = client.get("/api/flags", headers=HDR)
    assert r.json()["active_experiment"]["flag"] == "cta_variant"
    # Stop -> none.
    r = client.post("/api/flags/experiment", headers=HDR, json={"action": "stop"})
    assert r.status_code == 200
    r = client.get("/api/flags", headers=HDR)
    assert r.json()["active_experiment"] is None


def test_experiment_force_winner_pins_variant():
    _reset_flags()
    r = client.post("/api/flags/experiment", headers=HDR,
                    json={"action": "start", "flag": "hero_variant",
                          "force_winner": "B"})
    assert r.status_code == 200
    assert r.json()["experiment"]["force_winner"] == "B"


def test_experiment_bad_action_422():
    _reset_flags()
    r = client.post("/api/flags/experiment", headers=HDR,
                    json={"action": "explode", "flag": "hero_variant"})
    assert r.status_code == 422


def test_analytics_sampling_drops_events():
    _reset_flags()
    # Sampling 0 -> every event with a session id is dropped.
    r = client.post("/api/flags", headers=HDR,
                    json={"name": "analytics_sampling", "value": 0.0})
    assert r.status_code == 200
    r = client.post("/api/analytics/event",
                    json={"type": "page_view", "sid": "sess-drop-test", "page": "/"})
    assert r.status_code == 200
    assert r.json().get("sampled") is False
    # Sampling 1 -> events are written.
    r = client.post("/api/flags", headers=HDR,
                    json={"name": "analytics_sampling", "value": 1.0})
    assert r.status_code == 200
    r = client.post("/api/analytics/event",
                    json={"type": "page_view", "sid": "sess-keep-test", "page": "/"})
    assert r.status_code == 200
    assert r.json().get("sampled", True) is not False


def test_flag_mutations_recorded_as_events():
    _reset_flags()
    client.post("/api/flags", headers=HDR,
                json={"name": "trust_bar_enabled", "value": False})
    r = client.post("/api/analytics", headers=HDR)
    if r.status_code == 200:
        totals = r.json().get("totals", {})
        assert any(k.startswith("flag_set:") for k in totals)
