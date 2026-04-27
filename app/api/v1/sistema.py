import os
import time
from datetime import datetime, timedelta, timezone

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
    running_on_vercel = bool(os.getenv("VERCEL"))
    scheduler_mode = "external" if running_on_vercel else ("disabled" if settings.disable_scheduler else "embedded")
    scheduler_activo = False
    email_configured = bool(settings.smtp_user and settings.smtp_password)
    cron_protected = bool(
        settings.cron_secret
        or (settings.github_oidc_repository and settings.github_oidc_audience and settings.github_oidc_ref)
    )

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
                scheduler_activo = last_run.finished_at >= datetime.now(timezone.utc) - timedelta(hours=20)
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
        "scheduler_mode": scheduler_mode,
        "email_configured": email_configured,
        "cron_protected": cron_protected,
        "total_guias_bd": total_guias,
        "bd_conectada": bd_conectada,
        "mensaje": "Sistema operando normalmente" if overall == "ok" else "Sin conexión a base de datos",
    }
