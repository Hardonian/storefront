"""Tests for Bayesian Beta-Bernoulli Multi-Armed Bandit service."""

import sqlite3

from app.core.database import init_database
from app.services.bandit_service import (
    calculate_significance,
    choose_variant,
    evaluate_and_auto_promote,
    get_bandit_stats,
    record_conversion,
)


def test_bandit_trial_recording_and_conversion_reward(tmp_path):
    db = tmp_path / "bandit.db"
    init_database(db)

    # Choose variant for session 1
    v1 = choose_variant("hero_variant", "sess_user_1", db_path=str(db))
    assert v1 in ("A", "B")

    stats = get_bandit_stats("hero_variant", db_path=str(db))
    assert stats[v1]["trials"] == 1
    assert stats[v1]["conversions"] == 0

    # Record conversion for session 1
    converted = record_conversion("hero_variant", "sess_user_1", db_path=str(db))
    assert converted is True

    stats_after = get_bandit_stats("hero_variant", db_path=str(db))
    assert stats_after[v1]["conversions"] == 1
    assert stats_after[v1]["rate"] == 1.0


def test_bandit_significance_calculation():
    # Scenario: Variant B is clearly outperforming Variant A
    stats = {
        "A": {"trials": 100, "conversions": 5},   # 5% conv
        "B": {"trials": 100, "conversions": 35},  # 35% conv
    }
    probs = calculate_significance(stats, num_simulations=1000)
    assert probs["B"] > 0.99
    assert probs["A"] < 0.01


def test_bandit_auto_promotion(tmp_path, monkeypatch):
    import app.flags as flags

    db = tmp_path / "auto_promote.db"
    init_database(db)
    monkeypatch.setattr(flags, "DEFAULT_FLAG_PATH", tmp_path / "flags.json")

    # Seed 60 trials for A (2 conversions), 60 trials for B (30 conversions)
    with sqlite3.connect(str(db)) as conn:
        for i in range(60):
            conn.execute("INSERT INTO bandit_trials (experiment, variant, session_id, converted) VALUES ('hero_variant', 'A', ?, ?)", (f"a_{i}", 1 if i < 2 else 0))
            conn.execute("INSERT INTO bandit_trials (experiment, variant, session_id, converted) VALUES ('hero_variant', 'B', ?, ?)", (f"b_{i}", 1 if i < 30 else 0))

    winner = evaluate_and_auto_promote("hero_variant", db_path=str(db))
    assert winner == "B"
    exp = flags.get_active_experiment(path=tmp_path / "flags.json")
    assert exp is not None
    assert exp["force_winner"] == "B"
