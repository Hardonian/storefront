"""Test fixtures for storefront.

Ensures required runtime env vars are present before the app imports,
so `pytest` collection does not fail on a missing STOREFRONT_DOWNLOAD_SECRET.
The value here is a test-only placeholder; the live service supplies the
real secret via its systemd EnvironmentFile.
"""
import os

os.environ.setdefault("STOREFRONT_DOWNLOAD_SECRET", "test-download-secret-not-for-prod")
os.environ.setdefault("STOREFRONT_DB_PATH", "/home/scott/ai-lab/revenue-os/revenue-os.db")
