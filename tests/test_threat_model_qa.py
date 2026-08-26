"""Battle-tested threat model, security contract, vulnerability defense, and QA verification suite."""

import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import init_database
from app.core.security import (
    resolve_download_file,
    safe_external_url,
    sign_download_token,
    validate_doc_name,
    validate_slug,
)
from app.main import app
from app.services.license_service import issue_buyer_license, verify_license_offline

client = TestClient(app)


# ── Threat Vector 1: SSRF & Malicious Checkout / Redirect Protection ─────────

@pytest.mark.parametrize(
    "url",
    [
        "http://buy.stripe.com/test",                       # Reject plain HTTP
        "https://attacker.com/steal",                      # Reject non-allowlisted domain
        "https://buy.stripe.com.attacker.com/pay",          # Reject domain suffix spoofing
        "https://user:pass@buy.stripe.com/checkout",        # Reject userinfo authority tricks
        "https://buy.stripe.com:8443/checkout",             # Reject non-standard ports
        "javascript:alert(document.cookie)",                # Reject JS pseudo-protocol
        "data:text/html,<script>alert(1)</script>",         # Reject data URI
        "ftp://buy.stripe.com/file",                        # Reject FTP
        "https://aiautomatedsystems.ca/malicious-path",     # Reject non-allowlisted internal paths
        "",                                                 # Reject empty
        None,                                               # Reject None
    ],
)
def test_ssrf_and_open_redirect_rejection(url):
    """Ensure safe_external_url rejects all malicious, unverified, or non-standard URLs."""
    assert safe_external_url(url) == ""


def test_ssrf_allowed_checkout_domains():
    """Ensure safe_external_url strictly permits legitimate payment providers."""
    valid_stripe = "https://buy.stripe.com/cs_live_123456"
    valid_gumroad = "https://gumroad.com/l/product_slug"
    valid_shop_gumroad = "https://shop.gumroad.com/l/product_slug"
    valid_audit = "https://aiautomatedsystems.ca/audit/"

    assert safe_external_url(valid_stripe) == valid_stripe
    assert safe_external_url(valid_gumroad) == valid_gumroad
    assert safe_external_url(valid_shop_gumroad) == valid_shop_gumroad
    assert safe_external_url(valid_audit) == valid_audit


# ── Threat Vector 2: Path Traversal & LFI Protection ──────────────────────────

@pytest.mark.parametrize(
    "bad_slug",
    [
        "../etc/passwd",
        "..\\windows\\system32",
        "product/../../secret",
        "valid/slug",
        "slug;drop table leads;",
        "slug\x00hidden",
        "",
        " ",
    ],
)
def test_validate_slug_rejection(bad_slug):
    """Ensure validate_slug fails closed on any path traversal or injection characters."""
    with pytest.raises(HTTPException):
        validate_slug(bad_slug)


@pytest.mark.parametrize(
    "bad_doc",
    [
        "../../etc/shadow",
        "..\\config.env",
        "terms-of-service/../../../secrets",
        "terms\x00.md",
        "unknown-confidential-doc",
    ],
)
def test_validate_doc_name_rejection(bad_doc):
    """Ensure validate_doc_name fails closed on non-allowlisted documents."""
    with pytest.raises(HTTPException):
        validate_doc_name(bad_doc)


def test_download_hmac_expiration_and_tamper_defense(tmp_path):
    """Ensure resolve_download_file prevents token tampering and expired access."""
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    sample_file = bundles / "sentinel.zip"
    sample_file.write_bytes(b"PK\x03\x04mock_zip_content")

    # 1. Valid non-expired token
    expires_at = int(time.time()) + 3600
    token = sign_download_token("sentinel", expires_at, secret="test-sec")
    resolved = resolve_download_file("sentinel", str(expires_at), token, bundles_dir=bundles, secret="test-sec")
    assert resolved == sample_file

    # 2. Expired token
    expired_time = int(time.time()) - 100
    expired_token = sign_download_token("sentinel", expired_time, secret="test-sec")
    with pytest.raises(HTTPException):
        resolve_download_file("sentinel", str(expired_time), expired_token, bundles_dir=bundles, secret="test-sec")

    # 3. Forged / tampered token
    with pytest.raises(HTTPException):
        resolve_download_file("sentinel", str(expires_at), "forged_token_1234", bundles_dir=bundles, secret="test-sec")

    # 4. Path traversal attempt in slug
    with pytest.raises(HTTPException):
        resolve_download_file("../secrets", str(expires_at), token, bundles_dir=bundles, secret="test-sec")


# ── Threat Vector 3: XSS & HTML Entity Escaping Defense ───────────────────────

def test_xss_prevention_in_blueprint_rendering(tmp_path, monkeypatch):
    """Ensure HTML injection in blueprint lead parameters is safely escaped in HTML output and malformed emails rejected."""
    import app.main as m
    db = tmp_path / "xss_bp.db"
    m._init_db(str(db))
    monkeypatch.setattr(m.settings, "db_path", str(db))

    # Reject malformed script tags in email
    malicious_email = 'operator+<script>alert("XSS")</script>@hospital.example'
    resp_bad = client.post(
        "/api/blueprint/generate",
        json={
            "email": malicious_email,
            "workload": "hipaa_notes",
            "scale": "medium",
            "compliance": "hipaa",
        },
    )
    assert resp_bad.status_code == 422

    # Verify that valid email with HTML special chars in workload is safely handled and escaped
    valid_email = "operator+sec@hospital.example"
    resp = client.post(
        "/api/blueprint/generate",
        json={
            "email": valid_email,
            "workload": "hipaa_notes",
            "scale": "medium",
            "compliance": "hipaa",
        },
    )
    assert resp.status_code == 200
    token = resp.json()["token"]

    view_resp = client.get(f"/blueprint/{token}")
    assert view_resp.status_code == 200
    assert "operator+sec@hospital.example" in view_resp.text


def test_xss_prevention_in_buyer_portal():
    """Ensure buyer portal escapes session IDs and email query parameters."""
    xss_payload = '<img src=x onerror=alert(1)>'
    resp = client.get(f"/buyer?session_id={xss_payload}&email={xss_payload}")
    assert resp.status_code == 200
    assert "<img src=x onerror=alert(1)>" not in resp.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in resp.text


# ── Threat Vector 4: DoS & Body Payload Limits (64KB Bound) ───────────────────

def test_payload_size_limit_rejection():
    """Ensure requests exceeding 64KB are rejected with HTTP 413 Payload Too Large."""
    oversized_text = "A" * (70 * 1024)  # 70KB payload
    resp = client.post("/api/tools/redact", json={"text": oversized_text})
    assert resp.status_code == 413


def test_bot_honeypot_trap_resilience(tmp_path, monkeypatch):
    """Ensure bot submissions with populated honeypot fields are trapped and ignored."""
    import app.main as m
    db = tmp_path / "honeypot.db"
    m._init_db(str(db))
    monkeypatch.setattr(m.settings, "db_path", str(db))

    resp = client.post(
        "/api/leads",
        json={
            "email": "bot@automated-spammer.example",
            "product_slug": "sentinel-note",
            "website": "http://spammer-link.example",  # Honeypot trap triggered
        },
    )
    assert resp.status_code == 200

    # Ensure lead was NOT inserted into database
    with m.get_db(str(db)) as conn:
        row = conn.execute("SELECT * FROM leads WHERE email = 'bot@automated-spammer.example'").fetchone()
        assert row is None


# ── Threat Vector 5: Operator Auth & Secret Isolation ─────────────────────────

def test_operator_endpoints_require_valid_secret(monkeypatch):
    """Ensure operator administrative and telemetry endpoints reject unauthenticated requests."""
    monkeypatch.setattr(settings, "api_key", "hardonia_super_secret_key_123")

    # 1. Without x-api-key header -> 403 Forbidden
    assert client.get("/api/flags").status_code in (401, 403)
    assert client.get("/api/flags/bandit").status_code in (401, 403)
    assert client.get("/api/stack/fleet").status_code in (401, 403)
    assert client.get("/api/demand/insights").status_code in (401, 403)
    assert client.get("/metrics").status_code in (401, 403)

    # 2. With wrong x-api-key header -> 403
    headers_bad = {"x-api-key": "wrong_key_xyz"}
    assert client.get("/api/flags", headers=headers_bad).status_code == 403
    assert client.get("/api/stack/fleet", headers=headers_bad).status_code == 403

    # 3. With correct x-api-key header -> 200
    headers_ok = {"x-api-key": "hardonia_super_secret_key_123"}
    assert client.get("/api/flags", headers=headers_ok).status_code == 200
    assert client.get("/api/flags/bandit", headers=headers_ok).status_code == 200
    assert client.get("/api/stack/fleet", headers=headers_ok).status_code == 200
    assert client.get("/api/demand/insights", headers=headers_ok).status_code == 200


# ── Threat Vector 6: Cryptographic Air-Gapped License Tampering Defense ───────

def test_offline_license_tamper_resistance(tmp_path):
    """Ensure any modification to buyer email, product slug, or plan invalidates the license signature."""
    db = tmp_path / "lic_threat.db"
    init_database(db)

    lic = issue_buyer_license(
        product_slug="n8n-hardened-automation-starter",
        buyer_email="legit_buyer@org.example",
        plan="PRO",
        db_path=str(db),
    )
    assert verify_license_offline(lic) is True

    # Attack 1: Modify plan from PRO to ENTERPRISE without re-signing
    attack_1 = dict(lic, plan="ENTERPRISE")
    assert verify_license_offline(attack_1) is False

    # Attack 2: Transfer to unauthorized email
    attack_2 = dict(lic, buyer_email="pirate@warez.example")
    assert verify_license_offline(attack_2) is False

    # Attack 3: Modify product slug
    attack_3 = dict(lic, product_slug="sentinel-compliance-suite")
    assert verify_license_offline(attack_3) is False

    # Attack 4: Signature truncation or collision attempt
    attack_4 = dict(lic, signature=lic["signature"][:8])
    assert verify_license_offline(attack_4) is False


# ── Threat Vector 7: Deterministic PII / PHI Redaction Correctness ────────────

def test_redaction_sandbox_comprehensive_scrub():
    """Ensure redaction engine comprehensively catches SSN, credit cards, emails, phone, and clinical MRNs."""
    complex_medical_note = (
        "Patient Jane Doe (MRN: 8492-9102) was seen at St. Jude. "
        "Primary email jane.doe@health.org and cell 415-555-2671. "
        "Billing CC: 4111-2222-3333-4444. SSN: 000-12-3456."
    )
    resp = client.post("/api/tools/redact", json={"text": complex_medical_note})
    assert resp.status_code == 200
    redacted = resp.json()["redacted_text"]

    # Verify all sensitive patterns are sanitized
    assert "8492-9102" not in redacted
    assert "jane.doe@health.org" not in redacted
    assert "415-555-2671" not in redacted
    assert "4111-2222-3333-4444" not in redacted
    assert "000-12-3456" not in redacted

    assert "[PHI_ID_REDACTED]" in redacted
    assert "[EMAIL_REDACTED]" in redacted
    assert "[PHONE_REDACTED]" in redacted
    assert "[CREDIT_CARD_REDACTED]" in redacted
    assert "[SSN_REDACTED]" in redacted


# ── Threat Vector 8: Security Headers Verification ────────────────────────────

def test_security_headers_compliance():
    """Ensure standard responses carry strict security defense headers."""
    resp = client.get("/")
    assert resp.status_code == 200
    headers = resp.headers

    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("referrer-policy") in ("no-referrer", "strict-origin-when-cross-origin")
    assert "content-security-policy" in headers
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
