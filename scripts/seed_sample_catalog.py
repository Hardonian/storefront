#!/usr/bin/env python3
"""Seed sample products into storefront database for local development and testing."""

import sys
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import store
from app.core.config import settings

SAMPLE_PRODUCTS = [
    {
        "slug": "sentinel-compliance-suite",
        "name": "Sentinel Compliance & Policy Suite",
        "price": "Pro $149",
        "audience": "Healthcare & Legal Compliance Teams",
        "pain": "Cloud LLMs leak patient records and confidential corporate communications.",
        "offer": "100% air-gapped policy auditing and document redaction with verifiable SHA-256 signatures.",
        "checkout_url": "https://buy.stripe.com/test_sentinel",
        "gumroad_url": "https://shop.gumroad.com/l/sentinel",
        "readiness_score": 100,
        "category": "Governance",
    },
    {
        "slug": "comfyui-production-workflow-pack",
        "name": "ComfyUI Commercial Workflow Pack",
        "price": "Pro $49",
        "audience": "Digital Agencies & Solo Creators",
        "pain": "Broken custom nodes and version drift crash local diffusion pipelines.",
        "offer": "25 battle-tested, frozen-dependency ComfyUI workflows for high-resolution batch rendering.",
        "checkout_url": "https://buy.stripe.com/test_comfyui",
        "gumroad_url": "https://shop.gumroad.com/l/comfyui",
        "readiness_score": 98,
        "category": "Creative Workflows",
    },
    {
        "slug": "n8n-hardened-automation-starter",
        "name": "n8n Hardened Self-Hosted Starter",
        "price": "Pro $79",
        "audience": "Sovereign Operators & IT Directors",
        "pain": "Zapier and Make bill thousands monthly for repetitive webhook execution.",
        "offer": "Production docker-compose stack with UFW isolation, automated Postgres backups, and 15 pre-built templates.",
        "checkout_url": "https://buy.stripe.com/test_n8n",
        "gumroad_url": "https://shop.gumroad.com/l/n8n",
        "readiness_score": 96,
        "category": "Infrastructure",
    },
    {
        "slug": "hardonia-compute-api-access",
        "name": "Hardonia Sovereign GPU Farm Access",
        "price": "Starter $20",
        "audience": "AI Engineers & Quant Researchers",
        "pain": "Hyperscaler GPUs charge $3+/hr with intrusive telemetry and queue throttling.",
        "offer": "Dedicated bare-metal V100 32GB & P40 24GB GPU inference endpoints with prepaid meter billing.",
        "checkout_url": "https://buy.stripe.com/test_compute",
        "gumroad_url": "https://shop.gumroad.com/l/compute",
        "readiness_score": 100,
        "category": "Compute",
    },
]


def main() -> None:
    db_path = settings.db_path
    print(f"Seeding catalog into {db_path}...")
    store.init_db(db_path)

    for p in SAMPLE_PRODUCTS:
        store.upsert_product(p, db_path=db_path)
        print(f"  + Added/Updated: {p['name']} ({p['slug']}) - {p['price']}")

    print(f"\nCatalog successfully seeded with {len(SAMPLE_PRODUCTS)} products.")


if __name__ == "__main__":
    main()
