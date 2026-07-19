"""SQLite operations for the storefront — products + leads."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("/home/scott/ai-lab/revenue-os/revenue-os.db")

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


@contextmanager
def get_conn(db_path: Path | str = DEFAULT_DB_PATH):
    """Yield a sqlite3 connection with row factory; commit on success."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Create the leads table if it doesn't exist."""
    with get_conn(db_path) as conn:
        conn.execute(LEADS_DDL)


# ── Products ──────────────────────────────────────────────────────────────────

def list_products(db_path: Path | str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT slug, name, status, audience, pain, offer, price, "
            "checkout_url, gumroad_url, image_path, landing_path, "
            "readiness_score, created_at, updated_at, dashboard_url, "
            "dashboard_features "
            "FROM products ORDER BY readiness_score DESC, name ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_product(slug: str, db_path: Path | str = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT slug, name, status, audience, pain, offer, price, "
            "checkout_url, gumroad_url, image_path, landing_path, "
            "deliverable_path, readiness_score, "
            "dashboard_url, dashboard_features, "
            "created_at, updated_at FROM products WHERE slug = ?",
            (slug,),
        ).fetchone()
    return dict(row) if row else None


# ── Leads ─────────────────────────────────────────────────────────────────────

def create_lead(
    email: str,
    product_slug: str | None,
    source: str,
    notes: str | None = None,
    tag: str | None = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO leads (email, product_slug, source, notes, tag, status) "
            "VALUES (?, ?, ?, ?, ?, 'new')",
            (email, product_slug, source, notes, tag),
        )
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


def list_leads(db_path: Path | str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM leads ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]