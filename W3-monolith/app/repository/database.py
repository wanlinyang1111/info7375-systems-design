"""
Repository layer - database connection and session factory.

Pool is capped at DB_POOL_SIZE (default 20) - intentionally low so
students can hit the pool exhaustion ceiling during the Break phase.
"""
import logging
import os
import time
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

log = logging.getLogger(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
POOL_SIZE     = int(os.getenv("DB_POOL_SIZE", "20"))
MAX_OVERFLOW  = int(os.getenv("DB_POOL_MAX_OVERFLOW", "5"))

# -- Engine ----------------------------------------------------------------
# pool_pre_ping keeps stale connections from returning silently.
# pool_timeout is low (5s) so Break scenarios surface quickly in metrics.
engine = create_engine(
    DATABASE_URL,
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_timeout=5,           # seconds to wait for a connection - fail fast
    pool_recycle=300,         # recycle connections every 5 min
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency - yields a DB session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> dict:
    """Used by /health endpoint — measures actual DB round-trip latency."""
    start = time.time()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "ping_ms": round((time.time() - start) * 1000, 1),
        }
    except Exception as exc:
        log.error("db_health_check_failed", extra={"error": str(exc)})
        return {
            "status": "error",
            "ping_ms": None,
            "detail": str(exc),
        }


def get_pool_stats() -> dict:
    """Exposes connection pool metrics for /health and CloudWatch."""
    pool = engine.pool
    return {
        "pool_size":      pool.size(),
        "checked_out":    pool.checkedout(),
        "overflow":       pool.overflow(),
        "checked_in":     pool.checkedin(),
    }
