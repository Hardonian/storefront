"""Tests for interactive tools: Redaction Sandbox, Hardware Sizer, and Architecture Blueprints."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_redaction_sandbox_page_and_api():
    page = client.get("/tools/redaction-sandbox")
    assert page.status_code == 200
    assert "Sentinel Sovereign Redaction Sandbox" in page.text

    resp = client.post(
        "/api/tools/redact",
        json={"text": "Contact operator at test@example.com or (555) 123-4567. SSN: 111-22-3333."},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["items_redacted"] >= 3
    assert "[SSN_REDACTED]" in data["redacted_text"]
    assert "[EMAIL_REDACTED]" in data["redacted_text"]


def test_hardware_sizer_page_and_api():
    page = client.get("/tools/hardware-sizer")
    assert page.status_code == 200
    assert "Private LLM Hardware Sizer" in page.text

    calc = client.post(
        "/api/tools/size-hardware",
        json={"model_params_b": 70, "quant_bits": 4, "concurrent_users": 10, "context_length": 8192},
    )
    assert calc.status_code == 200
    data = calc.json()
    assert data["total_vram_gb"] > 40
    assert "Tesla V100" in data["gpu_topology"]
    assert data["monthly_savings_usd"] > 0
    assert data["recommended_bundle"] != ""


def test_dynamic_blueprint_generation_and_view(tmp_path, monkeypatch):
    import app.main as m
    db = tmp_path / "bp.db"
    m._init_db(str(db))
    monkeypatch.setattr(m.settings, "db_path", str(db))

    gen = client.post(
        "/api/blueprint/generate",
        json={
            "email": "director@healthorg.example",
            "workload": "hipaa_notes",
            "scale": "enterprise",
            "compliance": "hipaa",
        },
    )
    assert gen.status_code == 200
    data = gen.json()
    assert "token" in data
    assert data["blueprint"]["primary_package"] == "sentinel-compliance-suite"

    # View rendered HTML blueprint
    view = client.get(f"/blueprint/{data['token']}")
    assert view.status_code == 200
    assert "Sovereign Architecture Blueprint" in view.text
    assert "director@healthorg.example" in view.text
    assert "sentinel-compliance-suite" in view.text
