"""
OrderService - Week 4 Microservices

The core service. Owns:
  - All database tables (shared DB stepping stone)
  - Product catalog (GET /products/)
  - Order placement (POST /orders/) - calls PaymentService and ShippingService
  - Health check aggregating all downstream service health

What changed from Week 3:
  - PaymentStrategy.process() is now httpx.post(PAYMENT_SERVICE_URL/charge)
  - ShippingStrategy.calculate() is now httpx.post(SHIPPING_SERVICE_URL/quote)
  - The Strategy seam IS the service boundary - interface unchanged
  - Network timeout configuration is now an architectural decision
  - Downstream failures are now network failures, not in-process exceptions

What stayed the same:
  - SQLAlchemy models and connection pool
  - In-process LRU cache for product reads
  - Structured JSON logging
  - Order creation flow and validation

KEY TEACHING POINT - timeout configuration:
  PAYMENT_TIMEOUT_S and SHIPPING_TIMEOUT_S control how long
  OrderService waits for downstream services. Set too low -> false
  failures. Set too high -> slow cascade when a service is degraded.
  Students tune these in the Break phase.
"""
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from cachetools import LRUCache
from threading import Lock

# X-Ray: disabled in local Docker Compose (AWS_XRAY_SDK_ENABLED=false)
if os.getenv("AWS_XRAY_SDK_ENABLED", "true").lower() != "false":
    from aws_xray_sdk.core import xray_recorder, patch_all
    patch_all()
    xray_recorder.configure(service="OrderService")

# -- Logging ----------------------------------------------------------------
logger = logging.getLogger()
handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s"
))
logger.addHandler(handler)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

# -- Config -----------------------------------------------------------------
PORT                = int(os.getenv("PORT", "8000"))
POOL_SIZE           = int(os.getenv("DB_POOL_SIZE", "20"))
MAX_OVERFLOW        = int(os.getenv("DB_POOL_MAX_OVERFLOW", "5"))
PAYMENT_SERVICE_URL = os.environ["PAYMENT_SERVICE_URL"]
SHIPPING_SERVICE_URL= os.environ["SHIPPING_SERVICE_URL"]
PAYMENT_TIMEOUT_S   = float(os.getenv("PAYMENT_TIMEOUT_S", "5.0"))
SHIPPING_TIMEOUT_S  = float(os.getenv("SHIPPING_TIMEOUT_S", "3.0"))

# -- Database (shared with all services in Week 4) --------------------------
db_secret_str = os.environ.get("DB_SECRET", "{}")
db_secret     = json.loads(db_secret_str)

DATABASE_URL = (
    f"postgresql+psycopg2://{db_secret['username']}:{db_secret['password']}"
    f"@{db_secret['host']}:{db_secret['port']}/{db_secret['dbname']}"
)

engine = create_engine(
    DATABASE_URL,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_timeout=5,
    pool_recycle=300,
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# -- In-process cache (same as Week 3 - same limitation) -------------------
_cache: LRUCache = LRUCache(maxsize=500)
_timestamps: dict = {}
_cache_lock = Lock()
_hits = _misses = 0
CACHE_TTL = 300

def cache_get(key):
    global _hits, _misses
    with _cache_lock:
        ts = _timestamps.get(key)
        if ts and (time.time() - ts) < CACHE_TTL:
            val = _cache.get(key)
            if val is not None:
                _hits += 1
                return val
        _misses += 1
        return None

def cache_set(key, value):
    with _cache_lock:
        _cache[key] = value
        _timestamps[key] = time.time()

def cache_invalidate(key):
    with _cache_lock:
        _cache.pop(key, None)
        _timestamps.pop(key, None)

def cache_stats():
    total = _hits + _misses
    return {
        "hits": _hits, "misses": _misses,
        "hit_rate": round(_hits / total, 3) if total else 0,
        "size": len(_cache),
    }

# -- FastAPI app ------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("OrderService starting up",
             extra={"pool_size": POOL_SIZE,
                    "payment_url": PAYMENT_SERVICE_URL,
                    "shipping_url": SHIPPING_SERVICE_URL})
    Base.metadata.create_all(bind=engine, checkfirst=True)
    yield
    log.info("OrderService shutting down")

app = FastAPI(
    title="OrderService",
    description="Week 4 - Order management microservice",
    version="1.0.0",
    lifespan=lifespan,
)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    log.info("http_request", extra={
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "duration_ms": round((time.time() - start) * 1000, 1),
    })
    return response

# -- DB dependency ----------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -- Health - aggregates downstream service health --------------------------
@app.get("/health")
def health():
    # DB check
    try:
        start = time.time()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_result = {"status": "ok", "ping_ms": round((time.time()-start)*1000,1)}
    except Exception as e:
        db_result = {"status": "error", "detail": str(e)}

    # PaymentService check
    try:
        r = httpx.get(f"{PAYMENT_SERVICE_URL}/health", timeout=2.0)
        payment_result = r.json()
    except Exception as e:
        payment_result = {"status": "error", "detail": str(e)}

    # ShippingService check
    try:
        r = httpx.get(f"{SHIPPING_SERVICE_URL}/health", timeout=2.0)
        shipping_result = r.json()
    except Exception as e:
        shipping_result = {"status": "error", "detail": str(e)}

    pool = engine.pool
    overall = "ok"
    if db_result.get("status") == "error":
        overall = "error"
    elif payment_result.get("status") == "error":
        overall = "degraded"
    elif shipping_result.get("status") == "error":
        overall = "degraded"

    return {
        "status": overall,
        "service": "OrderService",
        "db": db_result,
        "cache": cache_stats(),
        "connection_pool": {
            "pool_size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        },
        "downstream": {
            "payment": payment_result,
            "shipping": shipping_result,
        },
    }

# -- Products routes (identical to Week 3) ---------------------------------
@app.get("/products/")
def list_products(
    limit: int = 20,
    offset: int = 0,
    category: str = None,
    db: Session = Depends(get_db),
):
    cache_key = f"products:{category}:{limit}:{offset}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    query = "SELECT id, name, price, stock_count, category, weight_kg FROM products"
    params = {}
    if category:
        query += " WHERE category = :category"
        params["category"] = category
    query += " LIMIT :limit OFFSET :offset"
    params.update({"limit": limit, "offset": offset})

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    result = [dict(r._mapping) for r in rows]
    cache_set(cache_key, result)
    return result

@app.get("/products/{product_id}")
def get_product(product_id: str, db: Session = Depends(get_db)):
    cache_key = f"product:{product_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, name, price, stock_count, category, weight_kg "
                 "FROM products WHERE id = :id"),
            {"id": product_id}
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    result = dict(row._mapping)
    cache_set(cache_key, result)
    return result

# -- Orders route - calls PaymentService and ShippingService ---------------
@app.post("/orders/", status_code=201)
async def create_order(request: Request, db: Session = Depends(get_db)):
    """
    Create an order. This route:
      1. Validates products and calculates totals (DB)
      2. Calls ShippingService.POST /quote (network)
      3. Calls PaymentService.POST /charge (network)
      4. Persists the order (DB)

    The Strategy seam from Week 3 IS the service call.
    The interface is identical - only the transport changed.
    """
    body = await request.json()
    user_id  = body.get("user_id")
    items    = body.get("items", [])
    shipping = body.get("shipping_method", "flat_rate")
    card     = body.get("card_token", "tok_test")

    if not user_id or not items:
        raise HTTPException(status_code=400, detail="user_id and items required")

    # Fetch products
    total_weight = 0.0
    subtotal     = 0.0
    line_items   = []

    with engine.connect() as conn:
        for item in items:
            row = conn.execute(
                text("SELECT id, price, stock_count, weight_kg FROM products WHERE id = :id"),
                {"id": item["product_id"]}
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404,
                                    detail=f"Product {item['product_id']} not found")
            qty = int(item.get("quantity", 1))
            if row.stock_count < qty:
                raise HTTPException(status_code=409,
                                    detail=f"Insufficient stock for {item['product_id']}")
            subtotal     += row.price * qty
            total_weight += row.weight_kg * qty
            line_items.append((row, qty))

    # -- Call ShippingService (network - was ShippingStrategy.calculate()) --
    import uuid
    order_id = str(uuid.uuid4())
    try:
        async with httpx.AsyncClient() as client:
            ship_resp = await client.post(
                f"{SHIPPING_SERVICE_URL}/quote",
                json={"weight_kg": total_weight,
                      "item_count": len(line_items),
                      "method": shipping},
                timeout=SHIPPING_TIMEOUT_S,
            )
        ship_resp.raise_for_status()
        quote = ship_resp.json()
    except httpx.TimeoutException:
        log.error("shipping_service_timeout", extra={"order_id": order_id})
        raise HTTPException(status_code=504,
                            detail="ShippingService timeout - try again")
    except httpx.HTTPStatusError as e:
        log.error("shipping_service_error", extra={"order_id": order_id,
                                                    "status": e.response.status_code})
        raise HTTPException(status_code=502, detail="ShippingService error")

    total_amount = round(subtotal + quote["cost"], 2)

    # -- Call PaymentService (network - was PaymentStrategy.process()) ------
    try:
        async with httpx.AsyncClient() as client:
            pay_resp = await client.post(
                f"{PAYMENT_SERVICE_URL}/charge",
                json={"amount": total_amount,
                      "card_token": card,
                      "order_id": order_id},
                timeout=PAYMENT_TIMEOUT_S,
            )
    except httpx.TimeoutException:
        log.error("payment_service_timeout", extra={"order_id": order_id})
        raise HTTPException(status_code=504,
                            detail="PaymentService timeout - try again")

    if pay_resp.status_code == 402:
        raise HTTPException(status_code=402, detail="Payment failed")
    if pay_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="PaymentService error")

    payment = pay_resp.json()

    # -- Persist order ------------------------------------------------------
    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO orders
                    (id, user_id, status, total_amount, shipping_cost,
                     payment_provider, shipping_method)
                    VALUES (:id, :user_id, :status, :total, :shipping_cost,
                            :provider, :method)"""),
            {
                "id": order_id, "user_id": user_id, "status": "confirmed",
                "total": total_amount, "shipping_cost": quote["cost"],
                "provider": payment.get("provider", "stub"),
                "method": quote["method"],
            }
        )
        for product, qty in line_items:
            conn.execute(
                text("""INSERT INTO order_items
                        (id, order_id, product_id, quantity, unit_price)
                        VALUES (:id, :order_id, :product_id, :qty, :price)"""),
                {
                    "id": str(uuid.uuid4()), "order_id": order_id,
                    "product_id": product.id, "qty": qty, "price": product.price,
                }
            )
            conn.execute(
                text("UPDATE products SET stock_count = stock_count - :qty WHERE id = :id"),
                {"qty": qty, "id": product.id}
            )
            cache_invalidate(f"product:{product.id}")

    log.info("order_created", extra={
        "order_id": order_id, "total": total_amount,
        "payment_provider": payment.get("provider"),
        "shipping_method": quote["method"],
    })

    return {
        "order_id": order_id,
        "status": "confirmed",
        "total_amount": total_amount,
        "shipping_cost": quote["cost"],
        "shipping_method": quote["method"],
        "payment_provider": payment.get("provider"),
        "transaction_id": payment.get("transaction_id"),
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
