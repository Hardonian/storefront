import os
import sqlite3
from pathlib import Path

# Tests must not depend on the operator's private filesystem or production DB.
_TEST_DB = Path(os.getenv("STOREFRONT_TEST_DB", "/tmp/storefront-test.db"))
_TEST_FLAGS = Path(os.getenv("STOREFRONT_TEST_FLAGS", "/tmp/storefront-flags.json"))
_TEST_LEGAL = Path(os.getenv("STOREFRONT_TEST_LEGAL", "/tmp/storefront-legal"))
_TEST_DB.unlink(missing_ok=True)
_TEST_FLAGS.unlink(missing_ok=True)
_TEST_LEGAL.mkdir(parents=True, exist_ok=True)
for _doc in ("terms-of-service", "privacy-policy", "refund-policy", "consent"):
    (_TEST_LEGAL / f"{_doc}.md").write_text(f"# {_doc}\n\nTest fixture only.", encoding="utf-8")
os.environ["STOREFRONT_DOWNLOAD_SECRET"] = "test-download-secret-not-for-prod"
os.environ["DB_PATH"] = str(_TEST_DB)
os.environ["STOREFRONT_FLAGS_PATH"] = str(_TEST_FLAGS)
os.environ["LEGAL_DIR"] = str(_TEST_LEGAL)

with sqlite3.connect(_TEST_DB) as _conn:
    _conn.executescript(
        """
        CREATE TABLE products (
            slug TEXT PRIMARY KEY, name TEXT, status TEXT, audience TEXT,
            pain TEXT, offer TEXT, price TEXT, checkout_url TEXT,
            gumroad_url TEXT, image_path TEXT, landing_path TEXT,
            deliverable_path TEXT, readiness_score INTEGER, dashboard_url TEXT,
            dashboard_features TEXT, created_at TEXT, updated_at TEXT
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
         datetime('now'), datetime('now')),
        ('ci-product', 'CI Product', 'ready', 'Operators', 'Drift',
         'A deterministic test catalog item', 'Pro $49',
         'https://buy.stripe.com/ci-test', '', '', '', '', 100, '', '',
         datetime('now'), datetime('now'));
        """
    )

# Keep backwards-compatible names for tests or local callers that import them.
os.environ.setdefault("STOREFRONT_DB_PATH", str(_TEST_DB))
