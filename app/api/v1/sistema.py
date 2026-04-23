import time
from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.shipment import Shipment
from app.models.tracking_run import TrackingRun

router = APIRouter(prefix="/system", tags=["system"])

_start_time = time.time()


@router.get("/health")
async def system_health():
    settings = get_settings()
    bd_conectada = False
    total_guias = 0
    ultima_consulta_tcc = None
    scheduler_activo = True

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            bd_conectada = True
            total_guias = (await session.execute(select(func.count()).select_from(Shipment))).scalar_one() or 0
            last_run = (
                await session.execute(
                    select(TrackingRun)
                    .where(TrackingRun.status == "completed")
                    .order_by(TrackingRun.finished_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if last_run and last_run.finished_at:
                ultima_consulta_tcc = last_run.finished_at.isoformat()
    except Exception:
        bd_conectada = False

    uptime = int(time.time() - _start_time)
    overall = "ok" if bd_conectada else "error"

    return {
        "status": overall,
        "version": "1.0.0",
        "uptime_seconds": uptime,
        "ultima_consulta_tcc": ultima_consulta_tcc,
        "scheduler_activo": scheduler_activo,
        "total_guias_bd": total_guias,
        "bd_conectada": bd_conectada,
        "mensaje": "Sistema operando normalmente" if overall == "ok" else "Sin conexión a base de datos",
    }
