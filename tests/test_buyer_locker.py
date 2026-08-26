"""Tests for Buyer Digital Locker and Cryptographic Air-Gapped Licensing."""

import json

from fastapi.testclient import TestClient

from app.core.database import init_database
from app.main import app
from app.services.license_service import get_buyer_entitlements, issue_buyer_license, verify_license_offline

client = TestClient(app)


def test_license_issuance_and_offline_verification(tmp_path):
    db = tmp_path / "lic.db"
    init_database(db)

    lic = issue_buyer_license(
        product_slug="sentinel-compliance-suite",
        buyer_email="buyer@enterprise.example",
        plan="PRO",
        db_path=str(db),
    )
    assert lic["license_key"].startswith("HK-PRO-SENTINEL-")
    assert verify_license_offline(lic) is True

    # Tampered license fails verification
    tampered = dict(lic)
    tampered["buyer_email"] = "evil@attacker.example"
    assert verify_license_offline(tampered) is False


def test_buyer_portal_and_license_download(tmp_path, monkeypatch):
    import app.main as m
    db = tmp_path / "portal.db"
    m._init_db(str(db))
    monkeypatch.setattr(m.settings, "db_path", str(db))

    # Issue test license
    lic = issue_buyer_license(
        product_slug="hardonia-compute-api-access",
        buyer_email="operator@lab.example",
        db_path=str(db),
    )

    # Portal shows entitlements
    portal = client.get("/buyer?email=operator@lab.example")
    assert portal.status_code == 200
    assert lic["license_key"] in portal.text
    assert ".lic" in portal.text

    # Download .lic certificate file
    cert_resp = client.get(f"/api/buyer/license/{lic['license_key']}.lic")
    assert cert_resp.status_code == 200
    assert cert_resp.headers["content-type"].startswith("application/json")
    cert = cert_resp.json()
    assert cert["license_key"] == lic["license_key"]
    assert cert["format"] == "HARDONIA-AIR-GAPPED-LICENSE-V1"
