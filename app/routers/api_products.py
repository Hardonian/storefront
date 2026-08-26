"""JSON REST API endpoints for product catalog."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.core.security import validate_slug
from app.services.product_service import get_product, list_products

router = APIRouter(prefix="/api/products", tags=["Products API"])


def _clean_product_for_api(p: dict[str, Any]) -> dict[str, Any]:
    """Strip internal filesystem paths to prevent information leaks."""
    cleaned = dict(p)
    cleaned.pop("landing_path", None)
    cleaned.pop("image_path", None)
    cleaned.pop("deliverable_path", None)
    return cleaned


@router.get("")
async def api_list_products(
    category: str | None = None,
    sort: str = Query("readiness", pattern="^(readiness|bestsellers)$"),
):
    """Retrieve JSON list of products with sanitized fields."""
    from app import store
    products = store.list_products(settings.db_path, sort=sort)
    cleaned = [_clean_product_for_api(p) for p in products]
    if category:
        clean_cat = category.strip().lower()
        cleaned = [p for p in cleaned if p.get("category", "").lower() == clean_cat]
    return {"products": cleaned, "count": len(cleaned)}


@router.get("/{slug}")
async def api_get_product(slug: str):
    """Retrieve single product metadata."""
    from app import store
    clean_slug = validate_slug(slug)
    product = store.get_product(clean_slug, settings.db_path)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return _clean_product_for_api(product)
