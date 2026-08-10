"""Security-regression tests for the storefront (host-aware canonical + traversal).

These run in-process (no live service needed) and lock the contract that the
cross-service live smoke also enforces. Keep in sync with
~/.hermes/scripts/security_regression_smoke.py.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_hardonia_host_canonical():
    home = client.get("/", headers={"host": "hardonia.store"})
    assert home.status_code == 200
    assert '<link rel="canonical" href="https://hardonia.store/">' in home.text
    assert '"name":"Hardonia Store"' in home.text
    # Consultancy host stays on its own origin (no Host-poisoning).
    home2 = client.get("/", headers={"host": "aiautomatedsystems.ca"})
    assert '<link rel="canonical" href="https://aiautomatedsystems.ca/">' in home2.text


def test_landing_traversal_blocked():
    for slug in ("..", "../", "..%2f", "foo/../../etc/passwd"):
        r = client.get(f"/landing/{slug}.html")
        assert r.status_code in (400, 404), f"traversal {slug!r} -> {r.status_code}"


def test_health_public():
    assert client.get("/health").status_code == 200
