"""
Health check - the first thing to curl in every Break scenario.

/health uses the SAME connection pool as application queries.
If the pool is exhausted, /health will also fail — this is correct
behaviour. A health check that bypasses the pool gives false confidence.

Students learn the difference between:
  - Liveness:  is the process running? (systemd handles this)
  - Readiness: can the process serve traffic? (this endpoint)
"""
import os
import platform
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from repository.database import check_db_connection, get_pool_stats
from services import cache

router = APIRouter()


@router.get("/health", tags=["health"])
def health_check():
    db = check_db_connection()
    pool = get_pool_stats()
    cache_stats = cache.stats()

    # Overall status: degraded if DB is slow, error if DB is down
    if db["status"] == "error":
        status = "error"
        http_status = 503
    elif db.get("ping_ms", 0) > 500:
        status = "degraded"
        http_status = 200   # still serving, but slow
    else:
        status = "ok"
        http_status = 200

    payload = {
        "status": status,
        "db": db,
        "connection_pool": pool,
        "cache": cache_stats,
        "instance": {
            "hostname": platform.node(),
            "python":   platform.python_version(),
            "pid":      os.getpid(),
        },
    }
    return JSONResponse(content=payload, status_code=http_status)
