#!/usr/bin/env python3
"""Simulate and verify sovereign stack bridge connectivity and telemetry feeds."""

import sys
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.core.database import init_all_services
from app.services.anomaly_detector import inspect_funnel_health
from app.services.demand_intelligence import get_demand_insights
from app.services.stack_bridge import (
    discover_bundle_manifests,
    get_live_fleet_telemetry,
    get_live_gpu_capacity,
)


def main() -> None:
    init_all_services()
    print("===============================================================")
    print("       Sovereign Stack Bridge & Fleet Telemetry Probe          ")
    print("===============================================================\n")

    # 1. Fleet Telemetry
    print("[1] Probing Hermes Ops Nerve Center & Service Units...")
    fleet = get_live_fleet_telemetry()
    print(f"    Status: {fleet.get('status', 'unknown')}")
    for unit, state in fleet.get("units", {}).items():
        print(f"    - {unit}: {state}")

    # 2. GPU Telemetry
    print("\n[2] Probing Hardonia Compute API & GPU Farm...")
    gpu = get_live_gpu_capacity()
    print(f"    GPU Model: {gpu.get('gpu')}")
    print(f"    VRAM Free: {gpu.get('free_pct')}%")
    print(f"    Nodes Online: {gpu.get('nodes_online')}")
    print(f"    Starting Rate: ${gpu.get('from_cents_per_hour', 0) / 100:.2f}/hr")

    # 3. Bundles Discovery
    print("\n[3] Scanning Bundles Directory Manifests...")
    bundles = discover_bundle_manifests()
    if bundles:
        for b in bundles:
            print(f"    - {b['slug']} (v{b['version']}) -> {b['size_mb']} MB")
    else:
        print("    (No zip bundle archives found in configured bundles directory)")

    # 4. Funnel Health & Anomaly Check
    print("\n[4] Inspecting Funnel Health & Anomaly Diagnostics...")
    health = inspect_funnel_health()
    print(f"    Health Status: {health.get('status')}")
    print(f"    Checkout Conversion Rate: {health.get('checkout_conversion_rate') * 100:.1f}%")
    if health.get("anomalies"):
        for a in health["anomalies"]:
            print(f"    ⚠️  {a}")

    # 5. Customer Demand Signals
    print("\n[5] Aggregating Customer Demand & Gap Intelligence...")
    demand = get_demand_insights()
    print(f"    Total Demand Signals: {demand.get('total_signals')}")
    for b in demand.get("breakdown", []):
        print(f"    - {b['category']}: {b['count']} inquiries ({b['share_pct']}%)")

    print("\n===============================================================")
    print("           Sovereign Stack Bridge Probe Complete               ")
    print("===============================================================")


if __name__ == "__main__":
    main()
