"""Core configuration for the Storefront service.

Centralized Pydantic settings, environment fallbacks, brand lookups,
and authorization helpers.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException, Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_TEMPLATES_DIR = str(Path(__file__).resolve().parent.parent / "templates")
DEFAULT_STATIC_DIR = str(BASE_DIR / "static")


class Settings(BaseSettings):
    """Application settings with environment variable precedence."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database paths
    db_path: str = Field(
        default_factory=lambda: os.getenv(
            "DB_PATH",
            str(Path.home() / "ai-lab" / "revenue-os" / "revenue-os.db"),
        )
    )
    analytics_db_path: str = Field(
        default_factory=lambda: os.getenv(
            "ANALYTICS_DB_PATH",
            "",
        )
    )

    # Directories
    landing_dir: str = Field(
        default_factory=lambda: os.getenv(
            "LANDING_DIR",
            str(Path.home() / "ai-lab" / "reports" / "landing"),
        )
    )
    legal_dir: str = Field(
        default_factory=lambda: os.getenv(
            "LEGAL_DIR",
            str(Path.home() / "ai-lab" / "legal"),
        )
    )
    templates_dir: str = DEFAULT_TEMPLATES_DIR
    static_dir: str = DEFAULT_STATIC_DIR
    content_drafts_dir: str = Field(
        default_factory=lambda: os.getenv(
            "CONTENT_DRAFTS_DIR",
            str(Path.home() / "ai-lab" / "reports" / "content" / "drafts"),
        )
    )
    bundles_dir: str = Field(
        default_factory=lambda: os.getenv(
            "BUNDLES_DIR",
            str(Path.home() / "ai-lab" / "store" / "bundles"),
        )
    )

    # Security & API credentials
    api_key: str = Field(default_factory=lambda: os.getenv("API_KEY", ""))
    download_secret: str = Field(
        default_factory=lambda: os.getenv("STOREFRONT_DOWNLOAD_SECRET", "dev-storefront-secret-key-replace-in-prod")
    )
    indexnow_key: str = Field(default_factory=lambda: os.getenv("INDEXNOW_KEY", ""))

    # Server configuration
    host: str = "0.0.0.0"
    port: int = 8020
    rate_limit_per_min: int = 20
    debug: bool = False

    # External integrations
    support_bot_url: str = Field(
        default_factory=lambda: os.getenv("SUPPORT_BOT_URL", "http://127.0.0.1:8070")
    )
    compute_api_url: str = Field(
        default_factory=lambda: os.getenv("COMPUTE_API_URL", "http://127.0.0.1:8000")
    )

    @property
    def effective_analytics_db_path(self) -> str:
        """Return analytics DB path if set and valid, otherwise fallback to primary db_path."""
        if self.analytics_db_path:
            return self.analytics_db_path
        return self.db_path


# Global singleton instance
settings = Settings()

# Public brand origins to prevent host poisoning
PUBLIC_BRANDS: dict[str, tuple[str, str]] = {
    "aiautomatedsystems.ca": ("https://aiautomatedsystems.ca", "AI Automated Systems"),
    "www.aiautomatedsystems.ca": ("https://aiautomatedsystems.ca", "AI Automated Systems"),
    "hardonia.store": ("https://hardonia.store", "Hardonia Store"),
    "www.hardonia.store": ("https://hardonia.store", "Hardonia Store"),
    "localhost": ("https://aiautomatedsystems.ca", "AI Automated Systems"),
    "127.0.0.1": ("https://aiautomatedsystems.ca", "AI Automated Systems"),
    "testserver": ("https://aiautomatedsystems.ca", "AI Automated Systems"),
}


def public_brand(request: Request | None) -> tuple[str, str]:
    """Return the canonical public origin and site name for an allowed storefront hostname.

    Unknown Host headers deliberately fall back to the consultancy origin so a
    forged Host cannot create arbitrary canonical URLs or poison SEO metadata.
    """
    if request is None or not request.url.hostname:
        return PUBLIC_BRANDS["aiautomatedsystems.ca"]
    host = request.url.hostname.lower().rstrip(".")
    return PUBLIC_BRANDS.get(host, PUBLIC_BRANDS["aiautomatedsystems.ca"])


def require_operator(x_api_key: str | None = Header(None)) -> None:
    """Fail closed for internal metrics, lead, and analytics surfaces.

    Uses constant-time comparison to prevent timing attacks.
    """
    if not settings.api_key or not x_api_key:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not secrets.compare_digest(x_api_key.strip(), settings.api_key.strip()):
        raise HTTPException(status_code=403, detail="Forbidden")
