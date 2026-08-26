#!/usr/bin/env python3
"""Evaluate active Multi-Armed Bandit experiments and report conversion statistical significance."""

import sys
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.services.bandit_service import calculate_significance, evaluate_and_auto_promote, get_bandit_stats


def main() -> None:
    print("===============================================================")
    print("      Bayesian Multi-Armed Bandit (Thompson Sampling) Audit    ")
    print("===============================================================\n")

    experiments = ["hero_variant", "cta_variant"]

    for exp in experiments:
        print(f"[*] Experiment: {exp}")
        stats = get_bandit_stats(exp)
        if not stats:
            print("    (No trial data recorded yet)\n")
            continue

        probs = calculate_significance(stats, num_simulations=5000)

        for variant, data in stats.items():
            rate = data["rate"] * 100
            p = probs.get(variant, 0.0) * 100
            print(f"    - Variant '{variant}': {data['trials']} trials, {data['conversions']} conversions ({rate:.2f}% CR) | P(Best) = {p:.1f}%")

        promoted = evaluate_and_auto_promote(exp)
        if promoted:
            print(f"    ⭐ Auto-Promoted Winning Variant: {promoted}")
        else:
            print("    Status: Continuous Exploration & Sampling")
        print()


if __name__ == "__main__":
    main()
