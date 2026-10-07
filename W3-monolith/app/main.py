"""
Info7375 Week 3 — Monolith Application
Entry point: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
"""
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

from repository.database import engine, check_db_connection, Base
from routes import products, orders, health

# -- Structured JSON logging ------------------------------------------------
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger()
handler = logging.StreamHandler()
file_handler = logging.FileHandler("logs/app.log")
formatter = jsonlogger.JsonFormatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s"
)
handler.setFormatter(formatter)
file_handler.setFormatter(formatter)
logger.addHandler(handler)
logger.addHandler(file_handler)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

log = logging.getLogger(__name__)


# -- Lifespan: startup / shutdown -------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting up - creating DB tables if not exist")
    Base.metadata.create_all(bind=engine)
    yield
    log.info("Shutting down")


# -- App --------------------------------------------------------------------
app = FastAPI(
    title="Info7375 Week 3 - Monolith",
    description="E-commerce order management - single deployable unit",
    version="1.0.0",
    lifespan=lifespan,
)


# -- Request logging middleware ---------------------------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration_ms = round((time.time() - start) * 1000, 1)
    log.info(
        "http_request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


# -- Error handler ----------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("unhandled_exception", extra={"error": str(exc), "path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# -- Routers ----------------------------------------------------------------
app.include_router(health.router)
app.include_router(products.router, prefix="/products", tags=["products"])
app.include_router(orders.router,   prefix="/orders",   tags=["orders"])
