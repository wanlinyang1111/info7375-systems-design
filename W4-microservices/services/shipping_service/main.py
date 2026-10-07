"""
ShippingService - Week 4 Microservices

What changed from Week 3:
  - ShippingStrategy is now a standalone HTTP service
  - POST /quote is the Strategy.calculate() method, over the network
  - All three strategies (flat_rate, weight_based, express) are here

What stayed the same:
  - Cost formulas are identical to Week 3
  - The interface: weight_kg + item_count -> cost + method + eta_days

This service is fully STATELESS - no database, no cache.
Every /quote call is independent. This makes it trivially
horizontally scalable and independently deployable.

The Break scenario for this service: introduce artificial latency
(SHIPPING_LATENCY_MS env var) and watch OrderService p99 climb
while GET /products stays completely unaffected. This is the
direct contrast to Week 3 Scenario C.
"""
import logging
import os
import time
from contextlib import asynccontextmanager


from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

if os.getenv("AWS_XRAY_SDK_ENABLED", "true").lower() != "false":
    from aws_xray_sdk.core import xray_recorder, patch_all
    patch_all()
    xray_recorder.configure(service="ShippingService")

logger = logging.getLogger()
handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s"
))
logger.addHandler(handler)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

PORT           = int(os.getenv("PORT", "8002"))
LATENCY_MS     = float(os.getenv("SHIPPING_LATENCY_MS", "10"))   # inject via env for Break


# -- Shipping strategies (same formulas as Week 3) --------------------------
STRATEGIES = {
    "flat_rate": {
        "calculate": lambda w, n: {"cost": 5.99, "method": "flat_rate", "eta_days": 5},
    },
    "weight_based": {
        "calculate": lambda w, n: {
            "cost": round(2.99 + w * 1.50, 2),
            "method": "weight_based",
            "eta_days": 3,
        },
    },
    "express": {
        "calculate": lambda w, n: {"cost": 19.99, "method": "express", "eta_days": 1},
    },
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("ShippingService starting up",
             extra={"latency_ms": LATENCY_MS,
                    "strategies": list(STRATEGIES.keys())})
    yield
    log.info("ShippingService shutting down")


app = FastAPI(
    title="ShippingService",
    description="Week 4 - Shipping calculation microservice",
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
        "status_code": response.status_code,
        "duration_ms": round((time.time() - start) * 1000, 1),
    })
    return response


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ShippingService",
        "available_methods": list(STRATEGIES.keys()),
        "injected_latency_ms": LATENCY_MS,
    }


@app.post("/quote")
async def quote(request: Request):
    """
    Calculate a shipping quote.

    This is ShippingStrategy.calculate() from Week 3, now a network call.

    Request body:
      - weight_kg: float
      - item_count: int
      - method: str  (flat_rate | weight_based | express)

    Returns:
      - cost: float
      - method: str
      - eta_days: int
    """
    start = time.time()
    body = await request.json()

    weight_kg  = float(body.get("weight_kg", 1.0))
    item_count = int(body.get("item_count", 1))
    method     = body.get("method", "flat_rate")

    if method not in STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown shipping method: {method}. "
                   f"Valid: {list(STRATEGIES.keys())}"
        )

    # Inject latency - used in Break Scenario B
    time.sleep(LATENCY_MS / 1000)

    result = STRATEGIES[method]["calculate"](weight_kg, item_count)
    duration_ms = round((time.time() - start) * 1000, 1)

    log.info("shipping_quoted", extra={
        "method": method,
        "weight_kg": weight_kg,
        "cost": result["cost"],
        "duration_ms": duration_ms,
    })

    return {**result, "duration_ms": duration_ms}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
