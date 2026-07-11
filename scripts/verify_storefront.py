#!/usr/bin/env python3
"""verify_storefront.py — revenue-surface integrity check.

Verifies the storefront catalog + checkout state for:
  - placeholder/empty/REPLACE_BEFORE_PUBLISH checkout URLs (won't convert)
  - duplicate Stripe/Gumroad links shared across multiple products (wrong
    attribution / accounting risk)
  - products flagged ready/draft-page that would render a Buy button to the
    wrong link
  - live service health for the buyer surface

Run:  python3 scripts/verify_storefront.py
Exit code: 0 = clean (warnings only), 1 = blocking issue found.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

DB = Path("/home/scott/ai-lab/revenue-os/revenue-os.db")
STOREFRONT = "http://127.0.0.1:8020"
CHECKOUT = "http://127.0.0.1:8012"

BLOCK = []
WARN = []


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def get(url: str, timeout: float = 4.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode()
    except Exception as e:  # noqa
        return None, str(e)


def main() -> int:
    c = conn()
    rows = c.execute(
        "SELECT slug, name, status, price, checkout_url, gumroad_url "
        "FROM products"
    ).fetchall()
    c.close()

    by_link: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        slug = r["slug"]
        cu = (r["checkout_url"] or "").strip()
        gu = (r["gumroad_url"] or "").strip()
        status = r["status"]

        # 1. Placeholder / missing links
        if status in ("ready", "draft-page"):
            effective = cu or gu
            if not effective or "REPLACE_BEFORE_PUBLISH" in effective:
                BLOCK.append(f"{slug}: published product has no real checkout link")
            elif effective.startswith("http") and "REPLACE" in effective:
                BLOCK.append(f"{slug}: checkout link still contains REPLACE_BEFORE_PUBLISH")

        # 2. Duplicate link tracking (dedupe per product so a row storing the
        #    same URL in both checkout_url and gumroad_url isn't double-counted)
        seen: set[str] = set()
        for eff in (cu, gu):
            if eff.startswith("http") and "REPLACE" not in eff and eff not in seen:
                seen.add(eff)
                by_link[eff].append(slug)

    for link, slugs in by_link.items():
        if len(slugs) > 1:
            WARN.append(
                f"DUPLICATE LINK {link} shared by {len(slugs)} products: {', '.join(slugs)}"
            )

    # 3. Live health checks
    for name, base in (("storefront", STOREFRONT), ("checkout", CHECKOUT)):
        status, body = get(f"{base}/health")
        if status != 200:
            BLOCK.append(f"{name} health check failed (status={status})")
        else:
            try:
                j = json.loads(body)
                if j.get("status") != "ok":
                    BLOCK.append(f"{name} reports unhealthy: {body[:80]}")
            except Exception:
                WARN.append(f"{name} health body not JSON")

    # 4. Buyer surface renders (not placeholder). The homepage is server-rendered
    #    from a Jinja template; CTAs only appear once products exist, so check the
    #    JSON catalog as the source of truth rather than grepping raw HTML.
    status, body = get(STOREFRONT + "/")
    if status == 200 and "<h1>Storefront</h1>" in body:
        BLOCK.append("storefront homepage is still serving the placeholder")
    status_j, jbody = get(STOREFRONT + "/api/products")
    if status_j == 200:
        try:
            j = json.loads(jbody)
            ready = [p for p in j.get("products", []) if p.get("status") in ("ready", "draft-page")]
            if not ready:
                WARN.append("no ready products to display on the catalog")
        except Exception:
            WARN.append("catalog JSON not parseable")

    # Report
    print("=== STOREFRONT REVENUE INTEGRITY ===")
    if WARN:
        print("\nWARNINGS (fix for trust/conversion):")
        for w in WARN:
            print(f"  - {w}")
    if BLOCK:
        print("\nBLOCKING ISSUES (won't convert / broken):")
        for b in BLOCK:
            print(f"  ! {b}")
    if not WARN and not BLOCK:
        print("  OK: catalog, links, and buyer surface all clean.")

    print(f"\nproducts={len(rows)}  duplicates={len([l for l,s in by_link.items() if len(s)>1])}")
    return 1 if BLOCK else 0


if __name__ == "__main__":
    sys.exit(main())
