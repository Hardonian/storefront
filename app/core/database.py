"""Resilient SQLite connection and schema management for Storefront."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from app.core.config import settings

logger = logging.getLogger("storefront.database")

PRODUCTS_DDL = """
CREATE TABLE IF NOT EXISTS products (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    audience TEXT,
    pain TEXT,
    offer TEXT,
    price TEXT NOT NULL,
    checkout_url TEXT,
    gumroad_url TEXT,
    image_path TEXT,
    landing_path TEXT,
    deliverable_path TEXT,
    readiness_score INTEGER DEFAULT 100,
    dashboard_url TEXT,
    dashboard_features TEXT,
    sales_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    stripe_sku TEXT
);
"""

LEADS_DDL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    product_slug TEXT,
    source TEXT NOT NULL DEFAULT 'landing',
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    tag TEXT
);
"""

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_slug TEXT,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'storefront',
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

COMMERCE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS commerce_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount_cents INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'usd',
    status TEXT DEFAULT 'succeeded',
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

FUNNEL_DDL = """
CREATE TABLE IF NOT EXISTS funnel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL CHECK(stage IN (
        'landing','tool_complete','offer_click','lead_start','validated_lead',
        'checkout_start','provider_payment','fulfillment'
    )),
    classification TEXT NOT NULL CHECK(classification IN (
        'synthetic','likely_bot','unknown','credible_human'
    )),
    classification_reason TEXT NOT NULL,
    referrer TEXT,
    campaign TEXT,
    page TEXT,
    product_slug TEXT,
    consent TEXT NOT NULL DEFAULT 'unset',
    amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(amount_cents >= 0),
    currency TEXT,
    provider_verified INTEGER NOT NULL DEFAULT 0 CHECK(provider_verified IN (0,1)),
    external_event_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_funnel_external_event
    ON funnel_events(external_event_id, stage)
    WHERE external_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_funnel_stage_class
    ON funnel_events(stage, classification, created_at);
"""


def _ensure_db_directory(db_path: str | Path) -> Path:
    """Ensure the parent directory for a SQLite database path exists."""
    p = Path(db_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Could not create database parent directory %s: %s", p.parent, e)
    return p


def get_sqlite_connection(db_path: str | Path, timeout: float = 15.0) -> sqlite3.Connection:
    """Open a SQLite connection with busy timeout and robust pragma setup."""
    p = _ensure_db_directory(db_path)
    conn = sqlite3.connect(str(p), timeout=timeout)
    conn.execute("PRAGMA busy_timeout = 15000")
    # Only set WAL mode if not in-memory
    if str(p) != ":memory:" and not str(p).startswith("file::memory:"):
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.OperationalError:
            pass
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def get_db(db_path: str | Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Yield a managed SQLite connection with automatic commit and rollback."""
    target_path = db_path or settings.db_path
    conn = get_sqlite_connection(target_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database(db_path: str | Path | None = None) -> None:
    """Initialize standard tables in the target database."""
    target_path = db_path or settings.db_path
    with get_db(target_path) as conn:
        conn.executescript(PRODUCTS_DDL)
        conn.executescript(LEADS_DDL)
        conn.executescript(EVENTS_DDL)
        conn.executescript(COMMERCE_EVENTS_DDL)


def init_analytics_database(analytics_path: str | Path | None = None) -> None:
    """Initialize telemetry tables in the analytics database."""
    target_path = analytics_path or settings.effective_analytics_db_path
    with get_db(target_path) as conn:
        conn.executescript(EVENTS_DDL)
        conn.executescript(FUNNEL_DDL)


def init_all_services() -> None:
    """Initialize all database tables and required filesystem directories at application startup."""
    try:
        init_database(settings.db_path)
    except Exception as e:
        logger.error("Failed to initialize primary database at %s: %s", settings.db_path, e)

    effective_analytics = settings.effective_analytics_db_path
    if effective_analytics:
        try:
            init_analytics_database(effective_analytics)
        except Exception as e:
            logger.error("Failed to initialize analytics database at %s: %s", effective_analytics, e)

    # Ensure required asset directories exist
    for dir_path in (settings.landing_dir, settings.legal_dir, settings.static_dir):
        try:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Could not ensure directory %s: %s", dir_path, e)
