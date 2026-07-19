import hashlib
import hmac
import os
import re
import time
from pathlib import Path

from fastapi import HTTPException

SECRET = os.environ.get('STOREFRONT_DOWNLOAD_SECRET', '')
if not SECRET or SECRET in ('change-me', 'changeme', ''):
    # Fail closed: never sign download tokens with a known/empty secret.
    raise RuntimeError(
        "STOREFRONT_DOWNLOAD_SECRET is not set (or still default). "
        "Download URLs are disabled until a real secret is configured."
    )
BASE_DIR = Path('/home/scott/ai-lab/store/bundles')


def sign(slug: str, expires_at: int) -> str:
    payload = f"{slug}:{expires_at}"
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]


def verify(slug: str, expires_at: int, token: str) -> bool:
    expected = sign(slug, expires_at)
    if not hmac.compare_digest(expected, token):
        return False
    return int(time.time()) < int(expires_at)


def build_download_url(slug: str, ttl_seconds: int = 3600) -> str:
    expires_at = int(time.time()) + ttl_seconds
    token = sign(slug, expires_at)
    return f"/download/{slug}?expires={expires_at}&token={token}"


def resolve_download(slug: str, expires: str, token: str):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", slug):
        raise HTTPException(status_code=400, detail='Invalid download path')
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail='Invalid or expired download link') from None
    if not verify(slug, expires_at, token):
        raise HTTPException(status_code=403, detail='Invalid or expired download link')
    base = BASE_DIR.resolve()
    path = (base / f"{slug}.zip").resolve()
    if path.parent != base:
        raise HTTPException(status_code=400, detail='Invalid download path')
    if not path.is_file():
        raise HTTPException(status_code=404, detail='Bundle not found')
    return path
