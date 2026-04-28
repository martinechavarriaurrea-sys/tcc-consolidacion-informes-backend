import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.report_file import ReportFile
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
    backend_email_configured = bool(settings.smtp_user and settings.smtp_password)
    email_mode = "external_outlook" if scheduler_mode == "external" else ("smtp" if backend_email_configured else "not_configured")
    email_configured = backend_email_configured or scheduler_mode == "external"
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
            latest_report_at = (
                await session.execute(
                    select(ReportFile.generated_at)
                    .where(ReportFile.report_type == "daily")
                    .order_by(ReportFile.generated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            latest_activity = None
            if last_run and last_run.finished_at:
                latest_activity = last_run.finished_at
            if latest_report_at and (latest_activity is None or latest_report_at > latest_activity):
                latest_activity = latest_report_at

            if latest_activity:
                if latest_activity.tzinfo is None:
                    latest_activity = latest_activity.replace(tzinfo=timezone.utc)
                ultima_consulta_tcc = latest_activity.isoformat()
                scheduler_activo = latest_activity >= datetime.now(timezone.utc) - timedelta(hours=20)
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
        "email_mode": email_mode,
        "cron_protected": cron_protected,
        "total_guias_bd": total_guias,
        "bd_conectada": bd_conectada,
        "mensaje": "Sistema operando normalmente" if overall == "ok" else "Sin conexión a base de datos",
    }
