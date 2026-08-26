"""SQLite operations for the storefront — products + leads."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.core.config import settings

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
def get_conn(db_path: Path | str | None = None):
    """Yield a sqlite3 connection with row factory; commit on success."""
    target_path = Path(db_path or settings.db_path)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    conn = sqlite3.connect(str(target_path), timeout=15.0)
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> None:
    """Create the leads table if it doesn't exist."""
    with get_conn(db_path) as conn:
        conn.execute(LEADS_DDL)


# ── Products ──────────────────────────────────────────────────────────────────

def list_products(db_path: Path | str | None = None, sort: str = "readiness") -> list[dict[str, Any]]:
    """List products with optional sort: 'readiness' (default) or 'bestsellers' (sales_count DESC)."""
    from app.services.product_service import list_products as _service_list_products
    return _service_list_products(db_path=db_path, sort=sort)


def get_product(slug: str, db_path: Path | str | None = None) -> dict[str, Any] | None:
    """Get single product by slug."""
    from app.services.product_service import get_product as _service_get_product
    return _service_get_product(slug=slug, db_path=db_path)


# ── Leads ─────────────────────────────────────────────────────────────────────

def create_lead(
    email: str,
    product_slug: str | None,
    source: str,
    notes: str | None = None,
    tag: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    with get_conn(db_path) as conn:
        conn.execute(LEADS_DDL)
        cur = conn.execute(
            "INSERT INTO leads (email, product_slug, source, notes, tag, status) "
            "VALUES (?, ?, ?, ?, ?, 'new')",
            (email, product_slug, source, notes, tag),
        )
        row = conn.execute(
            "SELECT * FROM leads WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


def list_leads(db_path: Path | str | None = None) -> list[dict[str, Any]]:
    with get_conn(db_path) as conn:
        conn.execute(LEADS_DDL)
        rows = conn.execute(
            "SELECT * FROM leads ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]