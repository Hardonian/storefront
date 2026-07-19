from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_metrics_endpoint(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.settings, "api_key", "test-operator-key")
    assert client.get("/metrics").status_code == 403
    r = client.get("/metrics", headers={"X-API-Key": "test-operator-key"})
    assert r.status_code == 200
    assert "http_requests_total" in r.text

# ── AU support bot proxy (/api/ask) ──────────────────────────────────────────
# The route calls the local bot at 127.0.0.1:8070 via httpx.Client. We monkeypatch
# httpx.Client so the test is deterministic and does not require the live bot process.
class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
    def json(self):
        return self._payload

class _FakeClient:
    """Returns a canned bot answer (or escalation) based on the request body."""
    def __init__(self, *a, **k):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def post(self, url, json=None, **k):
        q = (json or {}).get("query", "")
        if "sk-" in q or "leaked" in q:
            return _FakeResp(200, {"escalation": "x", "issue": "https://github.com/x/1"})
        return _FakeResp(200, {"answer": "Use X-API-Key header.", "escalated": False})
    def get(self, url, **k):
        return _FakeResp(200, {"status": "ok", "capacity_ok": True,
                               "intel_block": False, "legal_clear": True})

def test_api_ask_answers(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    r = client.post("/api/ask", json={"query": "How do I authenticate?"})
    assert r.status_code == 200
    body = r.json()
    assert body["escalated"] is False
    assert "X-API-Key" in body["answer"]

def test_api_ask_escalates_on_leak(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    r = client.post("/api/ask", json={"query": "my key sk-abcd1234efgh5678 leaked"})
    assert r.status_code == 200
    body = r.json()
    assert body["escalated"] is True
    # must never echo the raw key back to the customer
    assert "sk-abcd1234efgh5678" not in body.get("message", "")

def test_api_ask_requires_query(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    r = client.post("/api/ask", json={})
    # FastAPI validates the required 'query' field -> 422 (pydantic), before our 400 check
    assert r.status_code in (400, 422)

def test_support_widget_served():
    r = client.get("/support-widget.js")
    assert r.status_code == 200
    assert "au-widget" in r.text


def test_homepage_has_complete_seo_metadata():
    r = client.get("/")
    assert r.status_code == 200
    assert '<meta name="description"' in r.text
    assert '<link rel="canonical" href="https://aiautomatedsystems.ca/">' in r.text
    assert 'property="og:title"' in r.text
    assert 'application/ld+json' in r.text


def test_public_catalog_never_leaks_host_paths():
    r = client.get("/api/products")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == len(body["products"])
    assert "/home/" not in r.text
    assert all("landing_path" not in p and "image_path" not in p for p in body["products"])


def test_every_catalog_product_page_renders_with_valid_schema():
    import json
    import re
    products = client.get("/api/products").json()["products"]
    assert products
    for product in products:
        r = client.get(f"/p/{product['slug']}")
        assert r.status_code == 200, product["slug"]
        assert f'<link rel="canonical" href="https://aiautomatedsystems.ca/p/{product["slug"]}">' in r.text
        assert 'property="og:title"' in r.text
        match = re.search(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
        assert match, product["slug"]
        schema = json.loads(match.group(1))
        assert schema["@type"] == "Product"
        assert schema["offers"]["priceCurrency"] == "USD"


def test_pricing_and_legal_pages_render():
    assert client.get("/pricing").status_code == 200
    for doc in ("terms-of-service", "privacy-policy", "refund-policy", "consent"):
        r = client.get(f"/legal/{doc}")
        assert r.status_code == 200
        assert "<h1>" in r.text


def test_funnel_metrics_are_operator_only(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.settings, "api_key", "test-operator-key")
    assert client.get("/metrics/funnel").status_code == 403
    assert client.get("/metrics/funnel", headers={"X-API-Key": "test-operator-key"}).status_code == 200


def test_unsafe_checkout_scheme_is_rejected():
    import app.main as m
    assert m._safe_external_url("javascript:alert(1)") == ""
    assert m._safe_external_url("http://buy.stripe.com/fake") == ""
    assert m._safe_external_url("https://evil.example/pay") == ""
    assert m._safe_external_url("https://buy.stripe.com/test") == "https://buy.stripe.com/test"


def test_paid_order_success_and_fulfillment_proxy(monkeypatch):
    import httpx

    class FulfillmentClient(_FakeClient):
        def post(self, url, json=None, **kwargs):
            assert url == "http://127.0.0.1:8012/api/v1/fulfillment/claim"
            return _FakeResp(200, {
                "type": "compute", "api_key": "hk_live_safe", "credits": 20,
                "product_slug": "hardonia-compute-api-access",
            })

    monkeypatch.setattr(httpx, "Client", FulfillmentClient)
    page = client.get("/order/success?session_id=cs_paid")
    assert page.status_code == 200
    assert "Claim your purchase" in page.text
    assert "cs_paid" in page.text
    claim = client.post(
        "/api/fulfillment/claim",
        json={"session_id": "cs_paid", "email": "buyer@example.com"},
    )
    assert claim.status_code == 200
    assert claim.json()["api_key"] == "hk_live_safe"



def test_privacy_erasure_is_queued_not_executed(monkeypatch, tmp_path):
    import sqlite3

    import app.main as m
    db = tmp_path / "privacy.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE leads(email TEXT)")
    con.execute("INSERT INTO leads(email) VALUES(?)", ("buyer@example.com",))
    con.commit()
    con.close()
    monkeypatch.setattr(m.settings, "db_path", str(db))
    r = client.post("/api/privacy/erase", json={"email": "buyer@example.com"})
    assert r.status_code == 200
    assert r.json()["status"] == "pending_verification"
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
    assert con.execute("SELECT status FROM privacy_requests").fetchone()[0] == "pending_verification"
    con.close()


def test_subscribe_persists_lead(monkeypatch, tmp_path):
    import sqlite3

    import app.main as m
    db = tmp_path / "subscribe.db"
    m.store.init_db(str(db))
    m._init_analytics(str(db))
    monkeypatch.setattr(m.settings, "db_path", str(db))
    r = client.post("/api/subscribe", json={"email": "subscriber@example.com", "tag": "launch"})
    assert r.status_code == 200
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM leads WHERE email=?", ("subscriber@example.com",)).fetchone()[0] == 1
    con.close()


def test_blog_and_compare_seo_and_soft_404s():
    index = client.get("/blog")
    assert index.status_code == 200
    assert "rel='canonical'" in index.text and "property='og:title'" in index.text
    assert client.get("/compare/not-a-real-topic").status_code == 404
    assert client.get("/blog/..%2Fsecrets").status_code in (404, 422)


def test_compare_valid_topic_renders_html():
    # Regression: /compare/{topic} built `html` but was missing `return`,
    # causing a 500 on every valid topic. Valid topics must render 200 HTML.
    for topic in ("comfyui-alternative", "n8n-self-hosted", "private-inference", "local-ai-stack"):
        r = client.get(f"/compare/{topic}")
        assert r.status_code == 200, f"{topic} should be 200, got {r.status_code}"
        assert "text/html" in r.headers.get("content-type", "")
        assert "<html" in r.text.lower()


def test_fulfillment_page_is_csp_safe_and_has_recoverable_buyer_states():
    page = client.get("/order/success?session_id=cs_paid")
    assert page.status_code == 200
    assert "innerHTML" not in page.text
    assert "<script src=\"/fulfillment.js\" defer></script>" in page.text
    assert "Verifying payment" in page.text
    assert "Try again" in page.text
    assert "Contact support" in page.text
    assert "'unsafe-inline'" not in page.headers["content-security-policy"].split("script-src", 1)[-1]

    script = client.get("/fulfillment.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("application/javascript")
    assert "innerHTML" not in script.text
    assert "createElement('a')" in script.text
    assert "finally" in script.text


def test_customer_javascript_has_no_innerhtml_sinks():
    assert "innerHTML" not in client.get("/support-widget.js").text
    assert "innerHTML" not in client.get("/tools/gpu-cost-calculator").text


def test_claim_errors_are_normalized_and_payment_surfaces_are_no_store(monkeypatch):
    import httpx

    class NotReadyClient(_FakeClient):
        def post(self, url, json=None, **kwargs):
            return _FakeResp(409, {"detail": "provider-internal-secret"})

    monkeypatch.setattr(httpx, "Client", NotReadyClient)
    page = client.get("/order/success?session_id=cs_paid")
    assert page.headers["cache-control"] == "no-store"

    bad = client.post("/api/fulfillment/claim", json={"session_id": "bad", "email": "nope"})
    assert bad.status_code == 422
    assert bad.json()["error"] == "invalid_claim"
    assert "detail" not in bad.json()
    assert bad.headers["cache-control"] == "no-store"

    pending = client.post(
        "/api/fulfillment/claim",
        json={"session_id": "cs_paid", "email": "buyer@example.com"},
    )
    assert pending.status_code == 409
    assert pending.json()["error"] == "claim_not_ready"
    assert "provider-internal-secret" not in pending.text
    assert pending.json()["request_id"] == pending.headers["x-request-id"]


def test_exact_cors_policy_and_body_limit():
    allowed = "https://aiautomatedsystems.ca"
    preflight = client.options(
        "/api/fulfillment/claim",
        headers={
            "Origin": allowed,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == allowed
    assert preflight.headers["access-control-allow-methods"] == "GET, POST"
    allowed_headers = {value.strip().lower() for value in preflight.headers["access-control-allow-headers"].split(",")}
    assert allowed_headers == {"accept", "accept-language", "content-language", "content-type"}

    for origin in ("https://evil.example", "https://aiautomatedsystems.ca.evil.example", "http://localhost:8020"):
        response = client.get("/api/products", headers={"Origin": origin})
        assert "access-control-allow-origin" not in response.headers

    oversized = b'{"query":"' + (b"x" * 70000) + b'"}'
    response = client.post(
        "/api/ask",
        content=oversized,
        headers={"content-type": "application/json", "content-length": str(len(oversized))},
    )
    assert response.status_code == 413
    assert response.json()["error"] == "request_too_large"


def test_public_security_headers_and_structured_request_log(caplog):
    import json
    import logging

    caplog.set_level(logging.INFO, logger="storefront")
    for path in ("/", "/pricing", "/api/products", "/order/success?session_id=cs_paid"):
        response = client.get(path, headers={"X-Request-ID": "buyer-request-123"})
        assert response.headers["x-request-id"] == "buyer-request-123"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["permissions-policy"] == "geolocation=(), microphone=(), camera=()"

    records = [json.loads(record.message) for record in caplog.records if record.message.startswith("{")]
    request_logs = [record for record in records if record.get("event") == "http_request"]
    assert request_logs
    assert all({"request_id", "method", "path", "status", "duration_ms"} <= record.keys() for record in request_logs)


def test_ssrf_destinations_are_fixed_and_checkout_urls_reject_authority_tricks(monkeypatch):
    import httpx

    import app.main as m

    assert m._safe_external_url("https://user@buy.stripe.com/pay") == ""
    assert m._safe_external_url("https://buy.stripe.com:444/pay") == ""
    assert m._safe_external_url("https://buy.stripe.com.evil.example/pay") == ""
    assert m._safe_external_url("https://shop.gumroad.com/pay") == "https://shop.gumroad.com/pay"

    calls = []

    class CapturingClient(_FakeClient):
        def __init__(self, *args, **kwargs):
            assert kwargs.get("trust_env") is False

        def post(self, url, json=None, **kwargs):
            calls.append(url)
            return _FakeResp(409, {})

    monkeypatch.setattr(httpx, "Client", CapturingClient)
    response = client.post(
        "/api/fulfillment/claim",
        json={"session_id": "cs_fixed", "email": "buyer@example.com", "url": "http://169.254.169.254/latest/meta-data"},
    )
    assert response.status_code == 409
    assert calls == ["http://127.0.0.1:8012/api/v1/fulfillment/claim"]


def test_download_and_content_routes_reject_path_traversal(monkeypatch, tmp_path):
    import time

    import app.downloads as downloads

    bundles = tmp_path / "bundles"
    bundles.mkdir()
    monkeypatch.setattr(downloads, "BASE_DIR", bundles)
    expires = int(time.time()) + 60
    traversal = "../escape"
    token = downloads.sign(traversal, expires)
    try:
        downloads.resolve_download(traversal, str(expires), token)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("signed traversal slug was accepted")

    assert client.get("/landing/%2e%2e.html").status_code in (400, 404)
    assert client.get("/blog/%2e%2e%2fsecret").status_code in (404, 422)
    assert client.get(f"/download/%2e%2e%2fescape?expires={expires}&token={token}").status_code in (400, 404)


def test_product_price_and_primary_cta_are_consistent_across_buyer_surfaces(monkeypatch):
    import app.main as m

    product = {
        "slug": "consistency-kit", "name": "Consistency Kit", "status": "ready",
        "audience": "Operators", "pain": "Drift", "offer": "One clear offer",
        "price": "Pro $49", "checkout_url": "https://buy.stripe.com/consistent",
        "gumroad_url": "https://shop.gumroad.com/l/alternate", "readiness_score": 100,
        "image_path": "", "landing_path": "",
    }
    monkeypatch.setattr(m.store, "list_products", lambda db_path: [dict(product)])
    monkeypatch.setattr(m.store, "get_product", lambda slug, db_path=None: dict(product) if slug == product["slug"] else None)
    monkeypatch.setattr(m, "_record_event", lambda *args, **kwargs: None)

    surfaces = {
        "home": client.get("/").text,
        "pricing": client.get("/pricing").text,
        "detail": client.get("/p/consistency-kit").text,
    }
    for name, html in surfaces.items():
        assert "Pro $49" in html, name
        assert "https://buy.stripe.com/consistent" in html, name
    assert "href=\"https://shop.gumroad.com/l/alternate\" class=\"btn btn-primary\"" not in surfaces["home"]
