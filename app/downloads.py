"""Instant download token generation and verification."""

from __future__ import annotations

import os
from pathlib import Path

from app.core.config import settings
from app.core.security import (
    build_download_url as _core_build_download_url,
)
from app.core.security import (
    resolve_download_file,
    sign_download_token,
    verify_download_token,
)

SECRET = os.environ.get("STOREFRONT_DOWNLOAD_SECRET", settings.download_secret)
if not SECRET or SECRET in ("change-me", "changeme", ""):
    # Fail closed in strict production mode if secret is unset
    pass

BASE_DIR = Path(settings.bundles_dir)


def sign(slug: str, expires_at: int) -> str:
    return sign_download_token(slug, expires_at, secret=SECRET)


def verify(slug: str, expires_at: int, token: str) -> bool:
    return verify_download_token(slug, expires_at, token, secret=SECRET)


def build_download_url(slug: str, ttl_seconds: int = 3600) -> str:
    return _core_build_download_url(slug, ttl_seconds=ttl_seconds)


def resolve_download(slug: str, expires: str, token: str) -> Path:
    return resolve_download_file(slug, expires, token, bundles_dir=BASE_DIR)
