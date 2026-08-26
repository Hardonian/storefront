"""Security utilities: input sanitization, token signing, path defense."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
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
    # Normalize aliases to canonical document names
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
    if not clean or len(clean) > 254:
        raise HTTPException(status_code=422, detail="Invalid email address")
    if not _EMAIL_REGEX.match(clean):
        raise HTTPException(status_code=422, detail="Invalid email address")
    return clean


def safe_external_url(url: str | None) -> str | None:
    """Validate that an external checkout/partner URL uses HTTPS."""
    if not url:
        return None
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        return None
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


def resolve_download_file(slug: str, expires: str, token: str, bundles_dir: Path | str | None = None) -> Path:
    """Safely verify token and resolve artifact path with strict path traversal containment."""
    clean_slug = validate_slug(slug)
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="Invalid or expired download link") from None

    if not verify_download_token(clean_slug, expires_at, token):
        raise HTTPException(status_code=403, detail="Invalid or expired download link")

    base = Path(bundles_dir or settings.bundles_dir).resolve()
    target_path = (base / f"{clean_slug}.zip").resolve()

    # Containment check: prevent escaping base bundle directory
    try:
        target_path.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid download path") from None

    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="Bundle not found")

    return target_path
