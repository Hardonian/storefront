"""Database connection factory, schema migration, and SQLite pool optimization."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from app.core.config import settings

logger = logging.getLogger("storefront.database")

# DDL definitions for Storefront tables
CREATE_PRODUCTS_TABLE = """
CREATE TABLE IF NOT EXISTS products (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    audience TEXT,
    pain TEXT,
    offer TEXT,
    price TEXT,
    checkout_url TEXT,
    gumroad_url TEXT,
    readiness_score INTEGER DEFAULT 0,
    category TEXT DEFAULT 'general',
    landing_path TEXT,
    image_path TEXT,
    deliverable_path TEXT,
    version TEXT DEFAULT '1.0.0',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_LEADS_TABLE = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    product_slug TEXT,
    source TEXT DEFAULT 'landing',
    notes TEXT,
    status TEXT DEFAULT 'new',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_slug TEXT,
    event_type TEXT NOT NULL,
    source TEXT DEFAULT 'storefront',
    payload_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_PRIVACY_REQUESTS_TABLE = """
CREATE TABLE IF NOT EXISTS privacy_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_verification',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_BANDIT_TRIALS_TABLE = """
CREATE TABLE IF NOT EXISTS bandit_trials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment TEXT NOT NULL,
    variant TEXT NOT NULL,
    session_id TEXT NOT NULL,
    converted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bandit_exp ON bandit_trials(experiment, variant);
"""

CREATE_DEMAND_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS demand_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT DEFAULT 'unclassified',
    raw_query TEXT NOT NULL,
    intent_tags TEXT,
    source TEXT DEFAULT 'api_ask',
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_BUYER_LICENSES_TABLE = """
CREATE TABLE IF NOT EXISTS buyer_licenses (
    license_key TEXT PRIMARY KEY,
    product_slug TEXT NOT NULL,
    buyer_email TEXT NOT NULL,
    plan TEXT DEFAULT 'commercial',
    hardware_fingerprint TEXT DEFAULT 'any',
    signature TEXT NOT NULL,
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    is_active INTEGER DEFAULT 1
);
"""

CREATE_SYSTEM_ANOMALIES_TABLE = """
CREATE TABLE IF NOT EXISTS system_anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anomaly_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    description TEXT NOT NULL,
    metadata_json TEXT,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved INTEGER DEFAULT 0
);
"""

CREATE_BLUEPRINTS_TABLE = """
CREATE TABLE IF NOT EXISTS blueprints (
    token TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    workload TEXT NOT NULL,
    scale TEXT DEFAULT 'medium',
    compliance TEXT DEFAULT 'standard',
    blueprint_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_sqlite_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create a high-concurrency SQLite connection with WAL mode and generous busy timeout."""
    target_path = Path(db_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(target_path),
        timeout=15.0,  # 15s busy timeout
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row

    # Performance optimizations
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=15000;")
    conn.execute("PRAGMA foreign_keys=ON;")

    return conn


@contextmanager
def get_db(db_path: str | Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite transactions with auto-commit and rollback on error."""
    path = db_path or settings.db_path
    conn = get_sqlite_connection(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database(db_path: str | Path | None = None) -> None:
    """Initialize primary catalog, lead, license, and autonomous learning tables."""
    path = db_path or settings.db_path
    with get_db(path) as conn:
        conn.execute(CREATE_PRODUCTS_TABLE)
        conn.execute(CREATE_LEADS_TABLE)
        conn.execute(CREATE_PRIVACY_REQUESTS_TABLE)
        conn.executescript(CREATE_BANDIT_TRIALS_TABLE)
        conn.execute(CREATE_DEMAND_SIGNALS_TABLE)
        conn.execute(CREATE_BUYER_LICENSES_TABLE)
        conn.execute(CREATE_SYSTEM_ANOMALIES_TABLE)
        conn.execute(CREATE_BLUEPRINTS_TABLE)


def init_analytics_database(db_path: str | Path | None = None) -> None:
    """Initialize telemetry and funnel tables in the designated analytics SQLite instance."""
    path = db_path or settings.effective_analytics_db_path
    with get_db(path) as conn:
        conn.execute(CREATE_EVENTS_TABLE)
        from app.funnel_truth import init_funnel_schema
        init_funnel_schema(path)


def init_all_services() -> None:
    """Application startup initialization routine."""
    logger.info("Initializing Storefront SQLite schemas at %s", settings.db_path)
    init_database(settings.db_path)
    if settings.effective_analytics_db_path:
        init_analytics_database(settings.effective_analytics_db_path)

    # Ensure required runtime asset directories exist
    for dir_path in (
        settings.bundles_dir,
        settings.landing_dir,
        settings.legal_dir,
        settings.content_drafts_dir,
        settings.static_dir,
    ):
        try:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Could not ensure directory %s: %s", dir_path, e)
