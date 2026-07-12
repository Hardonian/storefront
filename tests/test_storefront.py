from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_metrics_endpoint():
    r = client.get("/metrics")
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
    import app.main as m
    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    r = client.post("/api/ask", json={"query": "How do I authenticate?"})
    assert r.status_code == 200
    body = r.json()
    assert body["escalated"] is False
    assert "X-API-Key" in body["answer"]

def test_api_ask_escalates_on_leak(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    r = client.post("/api/ask", json={"query": "my key sk-abcd1234efgh5678 leaked"})
    assert r.status_code == 200
    body = r.json()
    assert body["escalated"] is True
    # must never echo the raw key back to the customer
    assert "sk-abcd1234efgh5678" not in body.get("message", "")

def test_api_ask_requires_query(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m.httpx, "Client", _FakeClient)
    r = client.post("/api/ask", json={})
    # FastAPI validates the required 'query' field -> 422 (pydantic), before our 400 check
    assert r.status_code in (400, 422)

def test_support_widget_served():
    r = client.get("/support-widget.js")
    assert r.status_code == 200
    assert "au-widget" in r.text
