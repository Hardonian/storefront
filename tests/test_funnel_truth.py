import sqlite3

from fastapi.testclient import TestClient

from app.funnel_truth import (
    CLASS_CREDIBLE_HUMAN,
    CLASS_LIKELY_BOT,
    CLASS_SYNTHETIC,
    CLASS_UNKNOWN,
    CLASSIFICATIONS,
    STAGES,
    classify_request,
    funnel_summary,
    init_funnel_schema,
    record_funnel_event,
    sync_commerce_events,
)


def test_request_classification_is_conservative_and_does_not_treat_browser_ua_as_human():
    assert classify_request(user_agent="Mozilla/5.0") == (CLASS_UNKNOWN, "insufficient_evidence")
    assert classify_request(user_agent="Googlebot/2.1") == (CLASS_LIKELY_BOT, "automation_marker")
    assert classify_request(user_agent="Mozilla/5.0", synthetic=True) == (
        CLASS_SYNTHETIC,
        "explicit_synthetic",
    )
    assert classify_request(user_agent="Mozilla/5.0", validated_action=True) == (
        CLASS_CREDIBLE_HUMAN,
        "validated_action",
    )


def test_funnel_schema_preserves_bounded_context_without_fingerprinting(tmp_path):
    db = tmp_path / "funnel.db"
    init_funnel_schema(db)
    record_funnel_event(
        db,
        stage="offer_click",
        classification=CLASS_UNKNOWN,
        classification_reason="insufficient_evidence",
        referrer="https://search.example/result",
        campaign="launch-august",
        page="/p/private-ai",
        product="private-ai",
        consent="accepted",
    )

    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(funnel_events)")}
        row = conn.execute(
            "SELECT stage,classification,referrer,campaign,page,product_slug,consent "
            "FROM funnel_events"
        ).fetchone()

    assert {"ip", "ip_address", "user_agent", "fingerprint", "session_id"}.isdisjoint(columns)
    assert row == (
        "offer_click",
        CLASS_UNKNOWN,
        "https://search.example/result",
        "launch-august",
        "/p/private-ai",
        "private-ai",
        "accepted",
    )
    assert STAGES == (
        "landing",
        "tool_complete",
        "offer_click",
        "lead_start",
        "validated_lead",
        "checkout_start",
        "provider_payment",
        "fulfillment",
    )


def test_commercial_metrics_exclude_synthetic_and_likely_bot(tmp_path):
    db = tmp_path / "funnel.db"
    init_funnel_schema(db)
    for classification, amount in (
        (CLASS_SYNTHETIC, 9000),
        (CLASS_LIKELY_BOT, 8000),
        (CLASS_UNKNOWN, 7000),
        (CLASS_CREDIBLE_HUMAN, 6000),
    ):
        record_funnel_event(
            db,
            stage="provider_payment",
            classification=classification,
            classification_reason="fixture",
            product="private-ai",
            amount_cents=amount,
            currency="usd",
            provider_verified=True,
        )

    summary = funnel_summary(db)

    assert summary["classification_counts"] == {
        CLASS_CREDIBLE_HUMAN: 1,
        CLASS_LIKELY_BOT: 1,
        CLASS_SYNTHETIC: 1,
        CLASS_UNKNOWN: 1,
    }
    assert summary["commercial_metrics"] == {
        "provider_payments": 2,
        "provider_revenue_cents": 13000,
        "validated_leads": 0,
        "checkout_starts": 0,
        "fulfillments": 0,
    }
    assert summary["commercial_included_classes"] == [CLASS_CREDIBLE_HUMAN, CLASS_UNKNOWN]
    assert summary["commercial_excluded_classes"] == [CLASS_LIKELY_BOT, CLASS_SYNTHETIC]


def test_local_livemode_rows_never_become_provider_evidence(tmp_path):
    db = tmp_path / "commerce-funnel.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE commerce_events(
                event_id TEXT PRIMARY KEY, session_id TEXT, product_slug TEXT,
                amount_cents INTEGER, currency TEXT, payment_intent TEXT,
                status TEXT, raw_event TEXT, channel TEXT, claimed_at INTEGER)"""
        )
        conn.executemany(
            "INSERT INTO commerce_events VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "evt_real",
                    "cs_real",
                    "private-ai",
                    5000,
                    "usd",
                    "pi_real",
                    "fulfilled",
                    '{"livemode":true,"type":"checkout.session.completed"}',
                    "organic",
                    1,
                ),
                (
                    "evt_test",
                    "cs_test",
                    "private-ai",
                    9000,
                    "usd",
                    "pi_test",
                    "fulfilled",
                    '{"livemode":false,"type":"checkout.session.completed"}',
                    "synthetic-test",
                    1,
                ),
                (
                    "local-row",
                    "cs_live_looking",
                    "private-ai",
                    7000,
                    "usd",
                    "pi_live_looking",
                    "fulfilled",
                    "{}",
                    "unknown",
                    1,
                ),
            ],
        )

    assert sync_commerce_events(db) == 6
    summary = funnel_summary(db)

    assert summary["commercial_metrics"]["provider_payments"] == 0
    assert summary["commercial_metrics"]["provider_revenue_cents"] == 0
    assert summary["commercial_metrics"]["fulfillments"] == 0
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT external_event_id,stage,classification,provider_verified "
            "FROM funnel_events ORDER BY external_event_id,stage"
        ).fetchall()
    assert ("evt_real", "provider_payment", CLASS_UNKNOWN, 0) in rows
    assert ("evt_test", "provider_payment", CLASS_SYNTHETIC, 0) in rows
    assert ("local-row", "provider_payment", CLASS_UNKNOWN, 0) in rows


def test_server_ingestion_maps_stages_and_preserves_attribution_without_identity(tmp_path, monkeypatch):
    import app.main as main
    from app.funnel_truth import init_funnel_schema

    db = tmp_path / "server-funnel.db"
    monkeypatch.setattr(main.settings, "db_path", str(db))
    monkeypatch.setattr(main.settings, "analytics_db_path", str(db))
    # Ensure both the old analytics events table and the new funnel_events table
    # exist in the tmp db, since the app still writes _record_event -> events
    # while funnel_truth reads/writes funnel_events.
    main._init_analytics(db)
    init_funnel_schema(db)
    client = TestClient(main.app)
    client.cookies.set("hardonia_consent", "accepted")

    response = client.post(
        "/api/track",
        headers={"user-agent": "Googlebot/2.1", "referer": "https://search.example/result"},
        json={
            "event": "page_view",
            "page": "/p/private-ai",
            "slug": "private-ai",
            "utm_campaign": "launch-august",
        },
    )
    assert response.status_code == 200
    client.cookies.delete("hardonia_consent")

    lead = client.post(
        "/api/lead",
        headers={"user-agent": "Mozilla/5.0", "referer": "https://partner.example/article"},
        json={
            "email": "buyer@example.invalid",
            "product_slug": "private-ai",
            "utm_campaign": "partner-launch",
        },
    )
    assert lead.status_code == 200

    trapped = client.post(
        "/api/lead",
        headers={"user-agent": "Mozilla/5.0"},
        json={"email": "bot@example.invalid", "website": "spam", "product_slug": "private-ai"},
    )
    assert trapped.status_code == 200

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT stage,classification,referrer,campaign,page,product_slug,consent "
            "FROM funnel_events ORDER BY id"
        ).fetchall()

    assert rows == [
        (
            "landing",
            CLASS_LIKELY_BOT,
            "https://search.example/result",
            "launch-august",
            "/p/private-ai",
            "private-ai",
            "accepted",
        ),
        (
            "lead_start",
            CLASS_UNKNOWN,
            "https://partner.example/article",
            "partner-launch",
            "/api/lead",
            "private-ai",
            "unset",
        ),
        (
            "validated_lead",
            CLASS_CREDIBLE_HUMAN,
            "https://partner.example/article",
            "partner-launch",
            "/api/lead",
            "private-ai",
            "unset",
        ),
        ("lead_start", CLASS_LIKELY_BOT, None, None, "/api/lead", "private-ai", "unset"),
    ]


def test_operator_funnel_metrics_use_truth_schema(tmp_path, monkeypatch):
    import app.main as main

    db = tmp_path / "metrics-funnel.db"
    init_funnel_schema(db)
    for classification in CLASSIFICATIONS:
        record_funnel_event(
            db,
            stage="checkout_start",
            classification=classification,
            classification_reason="fixture",
        )
    monkeypatch.setattr(main.settings, "db_path", str(db))
    monkeypatch.setattr(main.settings, "analytics_db_path", str(db))
    monkeypatch.setattr(main.settings, "api_key", "operator-test-key")

    client = TestClient(main.app)
    response = client.get("/metrics/funnel", headers={"X-API-Key": "operator-test-key"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "privacy-funnel/v1"
    assert payload["commercial_metrics"]["checkout_starts"] == 2
    assert payload["commercial_stage_counts"]["checkout_start"] == 2
    assert payload["commercial_excluded_classes"] == [CLASS_LIKELY_BOT, CLASS_SYNTHETIC]
