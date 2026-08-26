"""Bayesian Beta-Bernoulli Multi-Armed Bandit (Thompson Sampling) for continuous conversion optimization."""

from __future__ import annotations

import logging
import random
from typing import Any

from app import flags as flag_engine
from app.core.config import settings
from app.core.database import get_db

logger = logging.getLogger("storefront.bandit")


def get_bandit_stats(experiment: str, db_path: str | None = None) -> dict[str, dict[str, Any]]:
    """Retrieve trial counts and conversion rewards per variant."""
    target_db = db_path or settings.db_path
    try:
        with get_db(target_db) as conn:
            rows = conn.execute(
                """SELECT variant,
                          COUNT(*) as trials,
                          SUM(converted) as conversions
                   FROM bandit_trials
                   WHERE experiment = ?
                   GROUP BY variant""",
                (experiment,),
            ).fetchall()

            stats: dict[str, dict[str, Any]] = {}
            for r in rows:
                v = r["variant"]
                trials = r["trials"] or 0
                convs = r["conversions"] or 0
                stats[v] = {
                    "trials": trials,
                    "conversions": convs,
                    "rate": round(convs / trials, 4) if trials > 0 else 0.0,
                }
            return stats
    except Exception as e:
        logger.warning("Error fetching bandit stats for %s: %s", experiment, e)
        return {}


def choose_variant(
    experiment: str,
    session_id: str,
    variants: list[str] | None = None,
    db_path: str | None = None,
) -> str:
    """Select the optimal variant for a session using Thompson Sampling."""
    target_variants = variants or ["A", "B"]
    stats = get_bandit_stats(experiment, db_path=db_path)

    # Draw from Beta(1 + successes, 1 + failures) for each variant
    samples: dict[str, float] = {}
    for v in target_variants:
        v_stats = stats.get(v, {"trials": 0, "conversions": 0})
        alpha = 1.0 + v_stats["conversions"]
        beta = 1.0 + (v_stats["trials"] - v_stats["conversions"])
        samples[v] = random.betavariate(alpha, max(beta, 0.001))

    # Pick the variant with the highest sampled probability
    chosen = max(samples, key=samples.get)

    # Record trial exposure in SQLite
    target_db = db_path or settings.db_path
    try:
        with get_db(target_db) as conn:
            conn.execute(
                "INSERT INTO bandit_trials (experiment, variant, session_id, converted) VALUES (?, ?, ?, 0)",
                (experiment, chosen, session_id),
            )
    except Exception as e:
        logger.warning("Failed to record bandit trial: %s", e)

    return chosen


def record_conversion(experiment: str, session_id: str, db_path: str | None = None) -> bool:
    """Record a positive conversion reward for the current session."""
    target_db = db_path or settings.db_path
    try:
        with get_db(target_db) as conn:
            cursor = conn.execute(
                """UPDATE bandit_trials
                   SET converted = 1
                   WHERE experiment = ? AND session_id = ? AND converted = 0""",
                (experiment, session_id),
            )
            converted = cursor.rowcount > 0
            if converted:
                # Check if this experiment has achieved auto-promotion significance
                evaluate_and_auto_promote(experiment, db_path=target_db)
            return converted
    except Exception as e:
        logger.warning("Failed to record bandit conversion: %s", e)
        return False


def calculate_significance(stats: dict[str, dict[str, Any]], num_simulations: int = 1000) -> dict[str, float]:
    """Calculate probability of each variant being the true optimal using Monte Carlo sampling."""
    if len(stats) < 2:
        return dict.fromkeys(stats, 1.0)

    wins: dict[str, int] = dict.fromkeys(stats, 0)
    variants = list(stats.keys())

    for _ in range(num_simulations):
        draws = {}
        for v in variants:
            c = stats[v]["conversions"]
            t = stats[v]["trials"]
            draws[v] = random.betavariate(1.0 + c, 1.0 + max(0, t - c))
        winner = max(draws, key=draws.get)
        wins[winner] += 1

    return {v: round(wins[v] / num_simulations, 4) for v in variants}


def evaluate_and_auto_promote(experiment: str, db_path: str | None = None) -> str | None:
    """Autonomously pin a winning variant if it achieves required sample size and confidence threshold."""
    stats = get_bandit_stats(experiment, db_path=db_path)
    if len(stats) < 2:
        return None

    # Check minimum trials per variant
    for v, s in stats.items():
        if s["trials"] < settings.bandit_min_trials:
            return None

    probabilities = calculate_significance(stats)
    for variant, prob in probabilities.items():
        if prob >= settings.bandit_significance_threshold:
            logger.info(
                "Bandit auto-promotion: Experiment '%s' achieved %s confidence for variant '%s'. Pinning winner.",
                experiment,
                prob,
                variant,
            )
            flag_engine.start_experiment(experiment, force_winner=variant, path=flag_engine.DEFAULT_FLAG_PATH)
            return variant

    return None
