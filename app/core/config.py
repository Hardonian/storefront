"""Application configuration and typed environment settings using Pydantic v2."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException, Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TEMPLATES_DIR = str(REPO_ROOT / "app" / "templates")
DEFAULT_STATIC_DIR = str(REPO_ROOT / "static")


class Settings(BaseSettings):
    """Runtime configuration model loaded from environment variables or .env files."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core identification & credentials
    api_key: str = Field(default_factory=lambda: os.getenv("API_KEY", ""))
    download_secret: str = Field(
        default_factory=lambda: os.getenv("DOWNLOAD_SECRET", "storefront_download_hmac_secret")
    )
    license_secret: str = Field(
        default_factory=lambda: os.getenv("LICENSE_SECRET", "storefront_license_signing_secret")
    )

    # SQLite Database paths
    db_path: str = Field(
        default_factory=lambda: os.getenv(
            "STOREFRONT_DB_PATH",
            str(Path.home() / "ai-lab" / "revenue-os" / "revenue-os.db"),
        )
    )
    analytics_db_path: str = Field(
        default_factory=lambda: os.getenv(
            "ANALYTICS_DB_PATH",
            "",
        )
    )
    revenue_os_db_path: str = Field(
        default_factory=lambda: os.getenv(
            "REVENUE_OS_DB_PATH",
            str(Path.home() / "ai-lab" / "revenue-os" / "revenue-os.db"),
        )
    )

    # Sovereign Stack & Hermes Connective Tissue
    ops_nerve_center_path: str = Field(
        default_factory=lambda: os.getenv(
            "OPS_NERVE_CENTER_PATH",
            str(Path.home() / ".hermes" / "scripts" / "ops-nerve-center.py"),
        )
    )
    deploy_all_script: str = Field(
        default_factory=lambda: os.getenv(
            "DEPLOY_ALL_SCRIPT",
            str(Path.home() / "ai-lab" / "scripts" / "bin" / "deploy-all.sh"),
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
            str(Path.home() / "ai-lab" / "bundles"),
        )
    )

    # Feature flags & Experiments
    flags_path: str = Field(
        default_factory=lambda: os.getenv(
            "STOREFRONT_FLAGS_PATH",
            str(REPO_ROOT / "flags.json"),
        )
    )
    bandit_min_trials: int = 50
    bandit_significance_threshold: float = 0.99

    # SEO & Verification
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
    fulfillment_url: str = Field(
        default_factory=lambda: os.getenv("FULFILLMENT_URL", "http://127.0.0.1:8012")
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
