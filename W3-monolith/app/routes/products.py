"""
Products route - 80% of load test traffic hits here.

Cache layer sits in front of DB reads. Students watch cache_hit_rate
climb in /health as the cache warms, then drop to 0 on EC2 restart
(cache stampede scenario).
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from repository.database import get_db
from models.orm import Product
from services import cache

log = logging.getLogger(__name__)
router = APIRouter()


class ProductResponse(BaseModel):
    id:          str
    name:        str
    description: str | None
    price:       float
    stock_count: int
    category:    str | None
    weight_kg:   float

    class Config:
        from_attributes = True


@router.get("/", response_model=list[ProductResponse])
def list_products(
    category: str | None = Query(None, description="Filter by category"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List products with optional category filter.
    Results are cached per (category, limit, offset) key for 5 minutes.
    Cache miss → DB query. Cache hit → no DB touch.
    """
    cache_key = f"products:{category}:{limit}:{offset}"
    cached = cache.get(cache_key)
    if cached is not None:
        log.debug("cache_hit", extra={"key": cache_key})
        return cached

    log.debug("cache_miss", extra={"key": cache_key})
    query = db.query(Product)
    if category:
        query = query.filter(Product.category == category)
    products = query.offset(offset).limit(limit).all()

    result = [ProductResponse.from_orm(p) for p in products]
    cache.set(cache_key, result)
    return result


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(product_id: str, db: Session = Depends(get_db)):
    cache_key = f"product:{product_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    result = ProductResponse.from_orm(product)
    cache.set(cache_key, result)
    return result
