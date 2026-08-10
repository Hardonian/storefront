"""Public Private AI Operations evaluation surface contracts."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_public_landing_is_canonical_and_evidence_bounded():
    response = client.get("/private-ai-operations")
    assert response.status_code == 200
    assert 'rel="canonical" href="https://aiautomatedsystems.ca/private-ai-operations"' in response.text
    assert "Request a scoped evaluation" in response.text
    assert "Pricing hypothesis" in response.text
    assert "NOT LIVE" in response.text
    assert "SOC 2" not in response.text


def test_public_demo_is_synthetic_only_and_assets_resolve():
    response = client.get("/private-ai-operations-demo/")
    assert response.status_code == 200
    assert "Synthetic demo data" in response.text
    assert 'content="noindex,follow"' in response.text
    for asset, content_type in (
        ("styles.css", "text/css"),
        ("app.js", "application/javascript"),
        ("demo-data.json", "application/json"),
    ):
        asset_response = client.get(f"/private-ai-operations-demo/{asset}")
        assert asset_response.status_code == 200
        assert content_type in asset_response.headers["content-type"]
    payload = client.get("/private-ai-operations-demo/demo-data.json").json()
    assert payload["data_classification"] == "synthetic-demo-only"
    assert payload["deterministic"] is True


def test_public_demo_asset_allowlist_rejects_other_paths():
    assert client.get("/private-ai-operations-demo/secret.env").status_code == 404
    assert client.get("/private-ai-operations-demo/../app/main.py").status_code == 404


def test_discovery_surfaces_include_public_landing():
    sitemap = client.get("/sitemap.xml", headers={"host": "aiautomatedsystems.ca"})
    assert sitemap.status_code == 200
    assert "https://aiautomatedsystems.ca/private-ai-operations" in sitemap.text
    llms = client.get("/llms.txt")
    assert llms.status_code == 200
    assert "https://aiautomatedsystems.ca/private-ai-operations" in llms.text
