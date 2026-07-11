import os
import time
import hmac
import hashlib
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
    if not verify(slug, expires, token):
        raise HTTPException(status_code=403, detail='Invalid or expired download link')
    path = BASE_DIR / f"{slug}.zip"
    if not path.exists():
        raise HTTPException(status_code=404, detail='Bundle not found')
    return path
