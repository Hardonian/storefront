"""Privacy-preserving, server-classified storefront funnel truth.

The schema intentionally stores no IP address, raw user agent, session identifier,
or device fingerprint. A normal browser user agent is never evidence of a human;
credible_human is reserved for a validated action or provider-verified outcome.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

CLASS_SYNTHETIC = "synthetic"
CLASS_LIKELY_BOT = "likely_bot"
CLASS_UNKNOWN = "unknown"
CLASS_CREDIBLE_HUMAN = "credible_human"
CLASSIFICATIONS = (
    CLASS_SYNTHETIC,
    CLASS_LIKELY_BOT,
    CLASS_UNKNOWN,
    CLASS_CREDIBLE_HUMAN,
)

STAGES = (
    "landing",
    "tool_complete",
    "offer_click",
    "lead_start",
    "validated_lead",
    "checkout_start",
    "provider_payment",
    "fulfillment",
)

COMMERCIAL_INCLUDED_CLASSES = (CLASS_CREDIBLE_HUMAN, CLASS_UNKNOWN)
COMMERCIAL_EXCLUDED_CLASSES = (CLASS_LIKELY_BOT, CLASS_SYNTHETIC)

_AUTOMATION_MARKERS = (
    "bot",
    "crawler",
    "spider",
    "slurp",
    "curl/",
    "python-requests",
    "httpx",
    "headless",
    "healthcheck",
    "monitor",
    "probe",
    "uptime",
)

FUNNEL_DDL = """
CREATE TABLE IF NOT EXISTS funnel_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL CHECK(stage IN (
        'landing','tool_complete','offer_click','lead_start','validated_lead',
        'checkout_start','provider_payment','fulfillment'
    )),
    classification TEXT NOT NULL CHECK(classification IN (
        'synthetic','likely_bot','unknown','credible_human'
    )),
    classification_reason TEXT NOT NULL,
    referrer TEXT,
    campaign TEXT,
    page TEXT,
    product_slug TEXT,
    consent TEXT NOT NULL DEFAULT 'unset',
    amount_cents INTEGER NOT NULL DEFAULT 0 CHECK(amount_cents >= 0),
    currency TEXT,
    provider_verified INTEGER NOT NULL DEFAULT 0 CHECK(provider_verified IN (0,1)),
    external_event_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_funnel_external_event
    ON funnel_events(external_event_id, stage)
    WHERE external_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_funnel_stage_class
    ON funnel_events(stage, classification, created_at);
"""


def classify_request(
    *,
    user_agent: str | None,
    synthetic: bool = False,
    honeypot: bool = False,
    validated_action: bool = False,
    provider_verified: bool = False,
) -> tuple[str, str]:
    """Return a conservative class and a coarse, non-identifying reason.

    Raw user-agent text is inspected transiently and is never returned or stored.
    Browser-looking UA text yields ``unknown``, not ``credible_human``.
    """
    if synthetic:
        return CLASS_SYNTHETIC, "explicit_synthetic"
    if honeypot:
        return CLASS_LIKELY_BOT, "honeypot"
    ua = str(user_agent or "").lower()
    if any(marker in ua for marker in _AUTOMATION_MARKERS):
        return CLASS_LIKELY_BOT, "automation_marker"
    if provider_verified:
        return CLASS_CREDIBLE_HUMAN, "provider_verified"
    if validated_action:
        return CLASS_CREDIBLE_HUMAN, "validated_action"
    return CLASS_UNKNOWN, "insufficient_evidence"


def init_funnel_schema(db_path: Path | str) -> None:
    with sqlite3.connect(str(db_path), timeout=15) as conn:
        conn.execute("PRAGMA busy_timeout=15000")
        conn.executescript(FUNNEL_DDL)


def _bounded(value: object, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def record_funnel_event(
    db_path: Path | str,
    *,
    stage: str,
    classification: str,
    classification_reason: str,
    referrer: str | None = None,
    campaign: str | None = None,
    page: str | None = None,
    product: str | None = None,
    consent: str | None = None,
    amount_cents: int = 0,
    currency: str | None = None,
    provider_verified: bool = False,
    external_event_id: str | None = None,
) -> None:
    """Persist one bounded event without identity, session, IP, or UA data."""
    if stage not in STAGES:
        raise ValueError(f"unsupported funnel stage: {stage}")
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"unsupported funnel classification: {classification}")
    if amount_cents < 0:
        raise ValueError("amount_cents must be non-negative")
    init_funnel_schema(db_path)
    with sqlite3.connect(str(db_path), timeout=15) as conn:
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute(
            """INSERT OR IGNORE INTO funnel_events
               (stage,classification,classification_reason,referrer,campaign,page,
                product_slug,consent,amount_cents,currency,provider_verified,external_event_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                stage,
                classification,
                _bounded(classification_reason, 80) or "unspecified",
                _bounded(referrer, 255),
                _bounded(campaign, 160),
                _bounded(page, 255),
                _bounded(product, 120),
                _bounded(consent, 24) or "unset",
                int(amount_cents),
                _bounded(currency, 12),
                int(provider_verified),
                _bounded(external_event_id, 200),
            ),
        )


def sync_commerce_events(db_path: Path | str) -> int:
    """Project commerce rows into provider stages without copying customer PII.

    A ``cs_live``/``pi_live``-looking identifier, local fulfilled status, or a
    locally stored raw event containing ``livemode: true`` is not independent
    provider proof. Explicit test evidence is retained as synthetic; every other
    local row remains unknown and cannot contribute provider revenue. Aggregate
    payment authority comes from the separate read-only Stripe API truth artifact.
    """
    init_funnel_schema(db_path)
    with sqlite3.connect(str(db_path), timeout=15) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='commerce_events'"
        ).fetchone()
        if not table:
            return 0
        columns = {row[1] for row in conn.execute("PRAGMA table_info(commerce_events)")}
        required = {"event_id", "product_slug", "amount_cents", "status"}
        if not required.issubset(columns):
            return 0
        selected = [
            name
            for name in (
                "event_id",
                "session_id",
                "product_slug",
                "amount_cents",
                "currency",
                "payment_intent",
                "status",
                "raw_event",
                "channel",
                "claimed_at",
                "customer_email",
            )
            if name in columns
        ]
        rows = [
            dict(zip(selected, row, strict=True))
            for row in conn.execute(f"SELECT {','.join(selected)} FROM commerce_events")
        ]

    attempted = 0
    synthetic_markers = ("synthetic", "test", "probe", "example.com", "example.org", "localhost")
    for row in rows:
        raw_text = str(row.get("raw_event") or "")
        try:
            raw = json.loads(raw_text) if raw_text else {}
        except (TypeError, json.JSONDecodeError):
            raw = {}
        evidence = " ".join(str(value or "") for value in row.values()).lower()
        explicitly_synthetic = raw.get("livemode") is False or any(
            marker in evidence for marker in synthetic_markers
        )
        provider_verified = False
        if explicitly_synthetic:
            classification, reason = CLASS_SYNTHETIC, "provider_test_evidence"
        else:
            classification, reason = CLASS_UNKNOWN, "local_provider_like_unverified"
        status = str(row.get("status") or "").lower()
        stages = []
        if status in {"paid", "completed", "fulfilled"}:
            stages.append("provider_payment")
        if status == "fulfilled" or row.get("claimed_at"):
            stages.append("fulfillment")
        for stage in stages:
            record_funnel_event(
                db_path,
                stage=stage,
                classification=classification,
                classification_reason=reason,
                campaign=row.get("channel"),
                page="provider",
                product=row.get("product_slug"),
                amount_cents=int(row.get("amount_cents") or 0) if stage == "provider_payment" else 0,
                currency=row.get("currency"),
                provider_verified=provider_verified,
                external_event_id=row.get("event_id"),
            )
            attempted += 1
    return attempted


def funnel_summary(db_path: Path | str) -> dict[str, object]:
    """Aggregate funnel truth, excluding synthetic/bot rows commercially."""
    sync_commerce_events(db_path)
    init_funnel_schema(db_path)
    with sqlite3.connect(str(db_path), timeout=15) as conn:
        class_counts = dict(
            conn.execute(
                "SELECT classification,COUNT(*) FROM funnel_events GROUP BY classification"
            ).fetchall()
        )
        stage_counts = dict(
            conn.execute("SELECT stage,COUNT(*) FROM funnel_events GROUP BY stage").fetchall()
        )
        placeholders = ",".join("?" for _ in COMMERCIAL_INCLUDED_CLASSES)
        eligible = conn.execute(
            f"""SELECT stage,COUNT(*),COALESCE(SUM(amount_cents),0)
                FROM funnel_events
                WHERE classification IN ({placeholders})
                  AND (stage NOT IN ('provider_payment','fulfillment') OR provider_verified = 1)
                GROUP BY stage""",
            COMMERCIAL_INCLUDED_CLASSES,
        ).fetchall()
    commercial = {stage: (count, cents) for stage, count, cents in eligible}
    return {
        "schema": "privacy-funnel/v1",
        "privacy": {
            "ip_addresses_stored": False,
            "raw_user_agents_stored": False,
            "session_identifiers_stored": False,
            "fingerprints_stored": False,
        },
        "classification_counts": dict(sorted(class_counts.items())),
        "stage_counts": {stage: int(stage_counts.get(stage, 0)) for stage in STAGES},
        "commercial_stage_counts": {
            stage: int(commercial.get(stage, (0, 0))[0]) for stage in STAGES
        },
        "commercial_included_classes": list(COMMERCIAL_INCLUDED_CLASSES),
        "commercial_excluded_classes": list(COMMERCIAL_EXCLUDED_CLASSES),
        "commercial_metrics": {
            "provider_payments": commercial.get("provider_payment", (0, 0))[0],
            "provider_revenue_cents": commercial.get("provider_payment", (0, 0))[1],
            "validated_leads": commercial.get("validated_lead", (0, 0))[0],
            "checkout_starts": commercial.get("checkout_start", (0, 0))[0],
            "fulfillments": commercial.get("fulfillment", (0, 0))[0],
        },
    }
