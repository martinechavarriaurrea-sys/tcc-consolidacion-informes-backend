from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.integrations.tcc.client import get_tcc_client

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """Verifica estado de la aplicación, base de datos e integración TCC."""
    checks: dict = {}

    # Database
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    # TCC integration
    try:
        provider = get_tcc_client()
        tcc_ok = await provider.health_check()
        checks["tcc_integration"] = "ok" if tcc_ok else "degraded"
    except Exception as exc:
        checks["tcc_integration"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return {
        "status": "healthy" if all_ok else "degraded",
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "checks": checks,
    }
