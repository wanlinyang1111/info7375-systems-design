"""
PaymentService - Week 4 Microservices

What changed from Week 3:
  - PaymentStrategy is no longer a class inside the monolith
  - It is now a standalone HTTP service
  - POST /charge is the Strategy.process() method, over the network
  - The interface is identical - only the transport changed

What stayed the same:
  - StubStrategy logic (fail rate, latency injection)
  - Idempotency via order_id
  - Structured JSON logging
  - /health endpoint

This service is STATELESS - no database connection.
Payment audit records would go to a payment-specific DB in
Week 6 when we split the data layer. For now, OrderService
owns all persistence.
"""
import json
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager

import boto3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

# -- X-Ray instrumentation --------------------------------------------------
if os.getenv("AWS_XRAY_SDK_ENABLED", "true").lower() != "false":
    from aws_xray_sdk.core import xray_recorder, patch_all
    patch_all()
    xray_recorder.configure(service="PaymentService")

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
FAIL_RATE   = float(os.getenv("PAYMENT_STUB_FAIL_RATE", "0"))
LATENCY_MS  = float(os.getenv("PAYMENT_STUB_LATENCY_MS", "50"))
PORT        = int(os.getenv("PORT", "8001"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("PaymentService starting up",
             extra={"fail_rate": FAIL_RATE, "latency_ms": LATENCY_MS})
    yield
    log.info("PaymentService shutting down")


app = FastAPI(
    title="PaymentService",
    description="Week 4 - Payment processing microservice",
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
        "service": "PaymentService",
        "fail_rate": FAIL_RATE,
        "latency_ms": LATENCY_MS,
    }


@app.post("/charge")
async def charge(request: Request):
    """
    Process a payment charge.

    This is the Strategy.process() method from Week 3, now running
    as a network endpoint. The interface is the same:
      - amount: float
      - card_token: str
      - order_id: str (idempotency key)

    Returns:
      - success: bool
      - transaction_id: str | None
      - provider: str
      - amount_charged: float
      - duration_ms: float
      - error: str | None
    """
    start = time.time()
    body = await request.json()

    amount     = float(body.get("amount", 0))
    card_token = body.get("card_token", "tok_test")
    order_id   = body.get("order_id", str(uuid.uuid4()))

    # Simulate network/processing latency
    time.sleep(LATENCY_MS / 1000)

    # Inject failure at configured rate
    if random.random() < FAIL_RATE:
        log.warning("payment_failed_injected",
                    extra={"order_id": order_id, "amount": amount})
        return JSONResponse(
            status_code=402,
            content={
                "success": False,
                "transaction_id": None,
                "provider": "stub",
                "amount_charged": 0,
                "duration_ms": round((time.time() - start) * 1000, 1),
                "error": "Stub: injected failure",
            }
        )

    transaction_id = f"stub_{uuid.uuid4().hex[:16]}"
    duration_ms = round((time.time() - start) * 1000, 1)

    log.info("payment_succeeded",
             extra={"order_id": order_id, "amount": amount,
                    "transaction_id": transaction_id, "duration_ms": duration_ms})

    return {
        "success": True,
        "transaction_id": transaction_id,
        "provider": "stub",
        "amount_charged": amount,
        "duration_ms": duration_ms,
        "error": None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
