#!/usr/bin/env python3
"""apply_ladder.py — write the Free→Pro→Premium→Enterprise ladder into revenue-os.

Backup taken by caller. For each product:
  checkout_url = Pro link, gumroad_url = Premium link,
  price = "Free to try · Pro $X · Premium $Y [· Enterprise contact]",
  status -> 'ready' for the previously-draft D products.
Amounts below mirror mint_revenue_ladder.py LADDER (single source of truth there).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = "/home/scott/ai-lab/revenue-os/revenue-os.db"
MANIFEST = "/home/scott/ai-lab/reports/ladder-manifest.json"

# slug -> {tier: (amount_display, is_monthly)}
AMOUNTS = {
    "autonomous-revenue-loop": {"pro": ("$29", False), "premium": ("$99", False), "enterprise": ("contact", False)},
    "ai-lab-power-bundle": {"pro": ("$99", False), "premium": ("$249", False)},
    "legal": {"pro": ("$49", False), "premium": ("$149", False)},
    "hardonia-compute-api-access": {"pro": ("$29/mo", True), "premium": ("$299/mo", True)},
    "comfyui-workflow-packs": {"pro": ("$29", False), "premium": ("$79", False)},
    "automation-retainer": {"pro": ("$750/mo", True), "premium": ("$1500/mo", True)},
    "ai-command-center-setup": {"premium": ("$997", False)},
    "comfyui-fashion-lookbook-kit": {"premium": ("$149", False)},
    "comfyui-node-starter-kit": {"premium": ("$99", False)},
    "comfyui-product-photo-kit": {"premium": ("$129", False)},
    "comfyui-thumbnail-creator-kit": {"premium": ("$119", False)},
    "apva-roi-benchmark": {"premium": ("$799", False)},
    "floyo-workflow-radar": {"premium": ("$49/mo", True)},
    "local-ai-lab-audit": {"premium": ("$997", False)},
    "repo-rescue-saas-audit": {"premium": ("$1500", False)},
    "ai-lab-notebook-packs": {"pro": ("$299", False), "premium": ("$99/mo", True)},
}

def main():
    man = json.loads(Path(MANIFEST).read_text())
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    updated = 0
    for slug, tiers in man.items():
        amt = AMOUNTS.get(slug, {})
        pro_url = tiers.get("pro") if isinstance(tiers.get("pro"), str) else None
        prem_url = tiers.get("premium") if isinstance(tiers.get("premium"), str) else None
        row = c.execute("SELECT status, checkout_url, gumroad_url FROM products WHERE slug=?", (slug,)).fetchone()
        if not row:
            print("SKIP no product:", slug)
            continue
        parts = ["Free to try"]
        if pro_url and "pro" in amt:
            parts.append(f"Pro {amt['pro'][0]}")
        if prem_url and "premium" in amt:
            parts.append(f"Premium {amt['premium'][0]}")
        if amt.get("enterprise"):
            parts.append("Enterprise contact")
        ladder = " · ".join(parts)
        new_status = "ready" if row["status"] in ("draft", "draft-page") else row["status"]
        c.execute("UPDATE products SET checkout_url=?, gumroad_url=?, price=?, status=? WHERE slug=?",
                  (pro_url or row["checkout_url"], prem_url or row["gumroad_url"], ladder, new_status, slug))
        updated += 1
        print(f"UPD {slug}: {new_status} | {ladder}")
    c.commit()
    c.close()
    print(f"\nTotal updated: {updated}")

if __name__ == "__main__":
    main()
