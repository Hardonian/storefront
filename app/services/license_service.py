"""Air-gapped cryptographic license issuance, verification, and entitlement management."""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from app.core.config import settings
from app.core.database import get_db
from app.core.security import generate_license_key, sign_license_payload, validate_slug

logger = logging.getLogger("storefront.license")


def issue_buyer_license(
    product_slug: str,
    buyer_email: str,
    plan: str = "PRO",
    hardware_fingerprint: str = "any",
    expires_days: int | None = 365,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Generate and persist a signed air-gapped software license."""
    clean_slug = validate_slug(product_slug)
    license_key = generate_license_key(clean_slug, buyer_email, plan)
    signature = sign_license_payload(clean_slug, buyer_email, plan)

    now = datetime.datetime.now(datetime.UTC)
    expires_at = (now + datetime.timedelta(days=expires_days)).isoformat() if expires_days else None

    target_db = db_path or settings.db_path
    try:
        with get_db(target_db) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO buyer_licenses
                   (license_key, product_slug, buyer_email, plan, hardware_fingerprint, signature, expires_at, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                (license_key, clean_slug, buyer_email.lower(), plan, hardware_fingerprint, signature, expires_at),
            )
    except Exception as e:
        logger.warning("Failed to store buyer license: %s", e)

    return {
        "license_key": license_key,
        "product_slug": clean_slug,
        "buyer_email": buyer_email,
        "plan": plan,
        "signature": signature,
        "hardware_fingerprint": hardware_fingerprint,
        "issued_at": now.isoformat(),
        "expires_at": expires_at,
        "is_active": True,
    }


def verify_license_offline(license_data: dict[str, Any]) -> bool:
    """Verify license certificate signature without connecting to external networks."""
    slug = license_data.get("product_slug", "")
    email = license_data.get("buyer_email", "")
    plan = license_data.get("plan", "PRO")
    sig = license_data.get("signature", "")

    if not slug or not email or not sig:
        return False

    expected_sig = sign_license_payload(slug, email, plan)
    return sig.strip().upper() == expected_sig.strip().upper()


def get_buyer_entitlements(buyer_email: str, db_path: str | None = None) -> list[dict[str, Any]]:
    """Retrieve all active licenses and downloadable products for a buyer."""
    target_db = db_path or settings.db_path
    try:
        with get_db(target_db) as conn:
            rows = conn.execute(
                """SELECT l.license_key, l.product_slug, l.plan, l.signature, l.issued_at, l.expires_at,
                          p.name, p.price, p.version
                   FROM buyer_licenses l
                   LEFT JOIN products p ON l.product_slug = p.slug
                   WHERE l.buyer_email = ? AND l.is_active = 1
                   ORDER BY l.issued_at DESC""",
                (buyer_email.lower().strip(),),
            ).fetchall()

            return [
                {
                    "license_key": r["license_key"],
                    "product_slug": r["product_slug"],
                    "product_name": r["name"] or r["product_slug"].replace("-", " ").title(),
                    "plan": r["plan"],
                    "version": r["version"] or "1.0.0",
                    "issued_at": r["issued_at"],
                    "expires_at": r["expires_at"],
                }
                for r in rows
            ]
    except Exception as e:
        logger.warning("Error fetching buyer entitlements for %s: %s", buyer_email, e)
        return []
