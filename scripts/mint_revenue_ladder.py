#!/usr/bin/env python3
"""mint_revenue_ladder.py — create the Free→Pro→Premium→Enterprise ladder.

Reads the tier model, mints missing Stripe Payment Links via the SDK, and
prints a manifest. DB writes happen in a SEPARATE step after human review of
this manifest (defence against wrong amounts on a live money account).

Tier model (grounded in existing revenue-os prices):
  free      -> lead magnet / free tier (no payment link; storefront delivers)
  pro       -> primary paid tier (one-time or /mo anchor)
  premium   -> add-on / higher tier (agency, team, done-for-you)
  enterprise-> contact / custom (mailto or contact page, no link)

Only mints links that don't already exist in the DB for that slug+tier.
"""
from __future__ import annotations

import json
import os
import sqlite3

import stripe

stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

DB = "/home/scott/ai-lab/revenue-os/revenue-os.db"

# slug -> dict(tier -> (display_name, amount_cents, currency, interval?))
# interval None => one-time. "month" => subscription.
LADDER = {
    # --- D: the 3 dead/empty products, now FILLED ---
    "autonomous-revenue-loop": {
        "pro":        ("Autonomous Revenue Loop — Pro OS", 2900, "usd", None),
        "premium":    ("Autonomous Revenue Loop — Premium + Implementation Day", 9900, "usd", None),
        "enterprise": None,  # retainer -> contact
    },
    "ai-lab-power-bundle": {
        "pro":        ("AI Lab Power Bundle", 9900, "usd", None),  # $99 bundle
        "premium":    ("AI Lab Power Bundle — Done-For-You Setup", 24900, "usd", None),
    },
    "legal": {
        "pro":        ("AI Legal Trust Pack — Pro (ToS/Privacy/Refunds)", 4900, "usd", None),
        "premium":    ("AI Legal Trust Pack — Premium (Review + Custom)", 14900, "usd", None),
    },
    # --- multi-tier dupes needing a Pro anchor ---
    "hardonia-compute-api-access": {
        "pro":        ("Hardonia Compute API — Growth (5M tokens/mo)", 2900, "usd", "month"),
        "premium":    ("Hardonia Compute API — Scale (100M tokens/mo)", 29900, "usd", "month"),
    },
    "comfyui-workflow-packs": {
        "pro":        ("ComfyUI Workflow Packs — Starter", 2900, "usd", None),
        "premium":    ("ComfyUI Workflow Packs — Pro Bundle", 7900, "usd", None),
    },
    "automation-retainer": {
        "pro":        ("Automation Retainer — Starter (1 workflow/mo)", 75000, "usd", "month"),
        "premium":    ("Automation Retainer — Growth (5 workflows/mo)", 150000, "usd", "month"),
    },
    # --- premium add-ons for natural high-tier products (mint if missing) ---
    "ai-command-center-setup": {
        "premium":    ("AI Command Center — Done-For-You", 99700, "usd", None),
    },
    "comfyui-fashion-lookbook-kit": {
        "premium":    ("ComfyUI Fashion Lookbook — Agency Pack", 14900, "usd", None),
    },
    "comfyui-node-starter-kit": {
        "premium":    ("ComfyUI Node Starter — Commercial License", 9900, "usd", None),
    },
    "comfyui-product-photo-kit": {
        "premium":    ("ComfyUI Product Photo — Studio Pack", 12900, "usd", None),
    },
    "comfyui-thumbnail-creator-kit": {
        "premium":    ("ComfyUI Thumbnail Creator — Channel Pack", 11900, "usd", None),
    },
    "apva-roi-benchmark": {
        "premium":    ("APVA ROI Benchmark — Team Benchmark", 79900, "usd", None),
    },
    "floyo-workflow-radar": {
        "premium":    ("Floyo Workflow Radar — Managed Monitor", 4900, "usd", "month"),
    },
    "local-ai-lab-audit": {
        "premium":    ("Local AI Lab Audit — With Implementation Day", 99700, "usd", None),
    },
    "repo-rescue-saas-audit": {
        "premium":    ("Repo Rescue — Fix Sprint", 150000, "usd", None),
    },
    "ai-lab-notebook-packs": {
        "pro":        ("AI Lab Notebook Packs — Bundle (5 packs)", 29900, "usd", None),
        "premium":    ("AI Lab Notebook Packs — Subscription", 9900, "usd", "month"),
    },
}

def already(slug, tier):
    c = sqlite3.connect(DB)
    row = c.execute("SELECT checkout_url, gumroad_url FROM products WHERE slug=?", (slug,)).fetchone()
    c.close()
    if not row:
        return False
    chk, gum = (row[0] or ""), (row[1] or "")
    return tier in (chk + " " + gum)

def mint(name, cents, currency, interval, slug, tier):
    meta = {"product_slug": slug, "tier": tier, "source": "hardonia-revenue-os"}
    p = stripe.Product.create(name=name, metadata=meta)
    price_kwargs = dict(product=p.id, unit_amount=cents, currency=currency, metadata=meta)
    if interval:
        price_kwargs["recurring"] = {"interval": interval}
    price = stripe.Price.create(**price_kwargs)
    pl = stripe.PaymentLink.create(line_items=[{"price": price.id, "quantity": 1}], metadata=meta)
    return pl.url

def main():
    manifest = {}
    for slug, tiers in LADDER.items():
        manifest[slug] = {}
        for tier, spec in tiers.items():
            if spec is None:
                manifest[slug][tier] = "contact"
                continue
            name, cents, cur, interval = spec
            try:
                url = mint(name, cents, cur, interval, slug, tier)
                manifest[slug][tier] = url
                print(f"MINTED {slug} [{tier}] {cents/100:.2f}{'/mo' if interval else ''} -> {url}")
            except Exception as e:
                manifest[slug][tier] = f"ERROR: {e}"
                print(f"FAIL  {slug} [{tier}]: {e}")
    print("MANIFEST_JSON=" + json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
