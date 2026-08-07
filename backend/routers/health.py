from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.cache_conf import redis_client
from config.db_conf import get_db


router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    checks = {"backend": "ok", "database": "unknown", "redis": "unknown"}

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    try:
        checks["redis"] = "ok" if await redis_client.ping() else "error"
    except Exception:
        checks["redis"] = "error"

    healthy = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "code": 200 if healthy else 503,
            "message": "healthy" if healthy else "degraded",
            "data": checks,
        },
    )
