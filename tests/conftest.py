import os
import sqlite3
import tempfile
from pathlib import Path

# Tests must not depend on the operator's private filesystem or production DB.
_TMP = Path(tempfile.gettempdir())
_TEST_DB = Path(os.getenv("STOREFRONT_TEST_DB", str(_TMP / "storefront-test.db")))
_TEST_FLAGS = Path(os.getenv("STOREFRONT_TEST_FLAGS", str(_TMP / "storefront-flags.json")))
_TEST_LEGAL = Path(os.getenv("STOREFRONT_TEST_LEGAL", str(_TMP / "storefront-legal")))

_TEST_DB.unlink(missing_ok=True)
_TEST_FLAGS.unlink(missing_ok=True)
_TEST_LEGAL.mkdir(parents=True, exist_ok=True)
for _doc in ("terms-of-service", "privacy-policy", "refund-policy", "consent"):
    (_TEST_LEGAL / f"{_doc}.md").write_text(f"# {_doc}\n\nTest fixture only.", encoding="utf-8")

os.environ["STOREFRONT_DOWNLOAD_SECRET"] = "test-download-secret-not-for-prod"
os.environ["DB_PATH"] = str(_TEST_DB)
os.environ["ANALYTICS_DB_PATH"] = str(_TEST_DB)
os.environ["STOREFRONT_FLAGS_PATH"] = str(_TEST_FLAGS)
os.environ["LEGAL_DIR"] = str(_TEST_LEGAL)

# Hermetic operator API key for authed surfaces (/api/flags, /api/analytics, ...).
# Env vars take precedence over the repo .env in pydantic-settings.
os.environ["API_KEY"] = "test-operator-key-not-for-prod"

# Load INDEXNOW_KEY from the gitignored .env if present, otherwise fall back to
# a deterministic hermetic CI key. Also materialize the static key file the app
# serves, so the test never depends on the operator's real .env or static dir.
# Set before any test module imports app.main so the module constant is correct.
_ENV = Path(__file__).resolve().parent.parent / ".env"
_indexnow_env_key = ""
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        if _line.startswith("INDEXNOW_KEY="):
            _indexnow_env_key = _line.split("=", 1)[1].strip()
            break
if _indexnow_env_key:
    os.environ["INDEXNOW_KEY"] = _indexnow_env_key
else:
    # Hermetic CI key: no operator .env needed; materialize the static file the
    # app serves so the IndexNow test is deterministic and portable.
    os.environ["INDEXNOW_KEY"] = "test-indexnow-key-not-for-prod"
    _STATIC = Path(__file__).resolve().parent.parent / "static"
    _STATIC.mkdir(parents=True, exist_ok=True)
    (_STATIC / f"{os.environ['INDEXNOW_KEY']}.txt").write_text(
        os.environ["INDEXNOW_KEY"], encoding="utf-8"
    )

with sqlite3.connect(_TEST_DB) as _conn:
    _conn.executescript(
        """
        CREATE TABLE products (
            slug TEXT PRIMARY KEY, name TEXT, status TEXT, audience TEXT,
            pain TEXT, offer TEXT, price TEXT, checkout_url TEXT,
            gumroad_url TEXT, image_path TEXT, landing_path TEXT,
            deliverable_path TEXT, readiness_score INTEGER, dashboard_url TEXT,
            dashboard_features TEXT, created_at TEXT, updated_at TEXT,
            stripe_sku TEXT
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_slug TEXT,
            event_type TEXT, source TEXT, payload_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
            product_slug TEXT, source TEXT NOT NULL DEFAULT 'landing',
            notes TEXT, status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, tag TEXT
        );
        CREATE TABLE commerce_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, amount_cents INTEGER DEFAULT 0,
            status TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO products VALUES
        ('comfyui-workflow-pack', 'ComfyUI Workflow Pack', 'ready', 'Creators',
         'Workflow drift', 'Reusable local image workflows', 'Pro $49',
         'https://buy.stripe.com/ci-test', '', '', '', '', 100, '', '',
         datetime('now'), datetime('now'), ''),
        ('ci-product', 'CI Product', 'ready', 'Operators', 'Drift',
         'A deterministic test catalog item', 'Pro $49',
         'https://buy.stripe.com/ci-test', '', '', '', '', 100, '', '',
         datetime('now'), datetime('now'), '');
        """
    )

# Keep backwards-compatible names for tests or local callers that import them.
os.environ.setdefault("STOREFRONT_DB_PATH", str(_TEST_DB))
