"""Security utilities: input sanitization, token signing, path defense, and air-gapped license generation."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException

from app.core.config import settings

_SLUG_REGEX = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$")
_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
_ALLOWED_LEGAL_DOCS = {
    "terms-of-service",
    "privacy-policy",
    "refund-policy",
    "consent",
    "terms",
    "privacy",
    "refund",
    "legal",
}

_ALLOWED_CHECKOUT_HOSTS = {
    "buy.stripe.com",
    "shop.gumroad.com",
    "gumroad.com",
    "aiautomatedsystems.ca",
    "hardonia.store",
}


def validate_slug(slug: str) -> str:
    """Validate a resource slug to prevent path traversal and injection."""
    slug = (slug or "").strip()
    if not slug or ".." in slug or "/" in slug or "\\" in slug or not _SLUG_REGEX.match(slug):
        raise HTTPException(status_code=400, detail="Invalid resource identifier")
    return slug


def validate_doc_name(doc: str) -> str:
    """Validate that a legal document name is in the allowlist."""
    cleaned = (doc or "").strip().lower().replace(".md", "")
    if cleaned not in _ALLOWED_LEGAL_DOCS:
        raise HTTPException(status_code=404, detail="Document not found")
    aliases = {
        "terms": "terms-of-service",
        "privacy": "privacy-policy",
        "refund": "refund-policy",
        "legal": "terms-of-service",
    }
    return aliases.get(cleaned, cleaned)


def validate_email_address(email: str) -> str:
    """Validate and normalize email addresses. Fail closed on malformed input."""
    clean = (email or "").strip().lower()
    if not clean or len(clean) > 254 or not _EMAIL_REGEX.match(clean):
        raise HTTPException(status_code=422, detail="Invalid email address")
    return clean


def safe_external_url(url: str | None) -> str:
    """Validate that an external checkout URL is safe and strictly allowlisted."""
    if not url:
        return ""
    url = url.strip()
    try:
        parsed = urlparse(url)
    except Exception:
        return ""

    if parsed.scheme != "https":
        return ""

    # Reject userinfo (e.g. user@host)
    if parsed.username or parsed.password:
        return ""

    # Reject non-standard ports
    if parsed.port not in (None, 443):
        return ""

    hostname = (parsed.hostname or "").lower()
    if hostname not in _ALLOWED_CHECKOUT_HOSTS:
        return ""

    # Special handling for internal host paths
    if hostname in ("aiautomatedsystems.ca", "hardonia.store"):
        if parsed.path != "/audit/":
            return ""

    return url


def sign_download_token(slug: str, expires_at: int, secret: str | None = None) -> str:
    """Sign a download token with HMAC-SHA256."""
    sec = secret or settings.download_secret
    payload = f"{slug}:{expires_at}"
    return hmac.new(sec.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def verify_download_token(slug: str, expires_at: int, token: str, secret: str | None = None) -> bool:
    """Verify an HMAC-SHA256 download token and check expiration."""
    expected = sign_download_token(slug, expires_at, secret)
    if not hmac.compare_digest(expected, token):
        return False
    return int(time.time()) < int(expires_at)


def build_download_url(slug: str, ttl_seconds: int = 86400) -> str:
    """Generate a tamper-proof download URL with expiration."""
    expires_at = int(time.time()) + ttl_seconds
    token = sign_download_token(slug, expires_at)
    return f"/download/{slug}?expires={expires_at}&token={token}"


def resolve_download_file(
    slug: str,
    expires: str,
    token: str,
    bundles_dir: Path | str | None = None,
    secret: str | None = None,
) -> Path:
    """Safely verify token and resolve artifact path with strict path traversal containment."""
    clean_slug = validate_slug(slug)
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid or expired download link") from None

    if not verify_download_token(clean_slug, expires_at, token, secret=secret):
        raise HTTPException(status_code=403, detail="Invalid or expired download link")

    base = Path(bundles_dir or settings.bundles_dir).resolve()
    target_path = (base / f"{clean_slug}.zip").resolve()

    try:
        target_path.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid download path") from None

    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="Bundle not found")

    return target_path


# ── Cryptographic Air-Gapped License Generation ───────────────────────────────

def sign_license_payload(slug: str, email: str, plan: str, secret: str | None = None) -> str:
    """Sign commercial license parameters with HMAC-SHA256."""
    sec = secret or settings.license_secret
    payload = f"HARDONIA-LIC:{slug.upper()}:{email.lower()}:{plan.upper()}"
    return hmac.new(sec.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24].upper()


def generate_license_key(slug: str, email: str, plan: str = "PRO") -> str:
    """Generate a formatted sovereign license key."""
    clean_slug = validate_slug(slug).upper().replace("-", "")[:8]
    sig = sign_license_payload(slug, email, plan)
    # Format: HK-PRO-SENTINEL-A1B2-C3D4-E5F6
    return f"HK-{plan.upper()}-{clean_slug}-{sig[:4]}-{sig[4:8]}-{sig[8:12]}-{sig[12:16]}"


def generate_blueprint_token() -> str:
    """Generate secure URL-safe blueprint reference token."""
    return f"bp_{secrets.token_urlsafe(16)}"
