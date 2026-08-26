"""JSON REST API endpoints for product catalog."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.core.security import validate_slug
from app.services.product_service import get_product, list_products, public_product

router = APIRouter(prefix="/api/products", tags=["Products API"])


@router.get("", response_model=list[dict[str, Any]])
async def api_list_products(
    category: str | None = None,
    sort: str = Query("readiness", pattern="^(readiness|bestsellers)$"),
):
    """Retrieve list of products with optional category filtering and sorting."""
    products = list_products(settings.db_path, sort=sort)
    if category:
        clean_cat = category.strip().lower()
        products = [p for p in products if p.get("category", "").lower() == clean_cat]
    return [public_product(p) for p in products]


@router.get("/{slug}", response_model=dict[str, Any])
async def api_get_product(slug: str):
    """Retrieve deep product metadata by slug."""
    clean_slug = validate_slug(slug)
    product = get_product(clean_slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return public_product(product)
