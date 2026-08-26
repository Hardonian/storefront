#!/usr/bin/env python3
"""Quick CLI health and status check for Storefront deployment."""

import json
import sys
import urllib.error
import urllib.request


def check_endpoint(url: str, label: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StorefrontHealthcheck/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            status = response.getcode()
            body = response.read().decode("utf-8")
            if status == 200:
                print(f"[OK] {label} ({url}) -> HTTP 200")
                try:
                    data = json.loads(body)
                    if "status" in data:
                        print(f"     Status: {data['status']}")
                except json.JSONDecodeError:
                    pass
                return True
            else:
                print(f"[WARN] {label} returned HTTP {status}")
                return False
    except urllib.error.URLError as e:
        print(f"[FAIL] {label} ({url}) -> {e}")
        return False


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8020"
    print(f"Checking Storefront at {base}...\n")

    h_ok = check_endpoint(f"{base}/health", "Liveness Probe")
    s_ok = check_endpoint(f"{base}/status.json", "System Status Snapshot")
    p_ok = check_endpoint(f"{base}/api/products", "Public Catalog API")

    if h_ok and s_ok and p_ok:
        print("\nAll Storefront health gates passed! (Status: GREEN)")
        sys.exit(0)
    else:
        print("\nOne or more health gates failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
