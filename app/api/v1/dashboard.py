from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api.deps import get_report_service
from app.schemas.dashboard import DashboardSummary
from app.services.report_service import ReportService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(svc: ReportService = Depends(get_report_service)):
    return await svc.get_dashboard_summary()


@router.get("/stats")
async def get_stats(svc: ReportService = Depends(get_report_service)):
    s = await svc.get_dashboard_summary()
    proxima_reporte = _next_report_run()
    return {
        "total_activas": s.total_active,
        "total_entregadas": s.total_delivered_today,
        "con_novedad": s.total_with_issues,
        "sin_movimiento": s.total_no_movement_72h,
        "monitoreadas_hoy": s.total_active,
        "ultima_ejecucion": s.last_tracking_run.isoformat() if s.last_tracking_run else None,
        "proxima_ejecucion": proxima_reporte,
        "proxima_reporte": proxima_reporte,
        "proxima_alerta": _next_alert_run(),
        "proxima_limpieza": _next_cleanup_run(),
        "estado_automatizacion": "ejecutado" if s.last_tracking_run else "programado",
    }


def _next_report_run() -> str:
    import datetime as dt
    now = datetime.now(timezone.utc)
    candidates: list[datetime] = []

    # Daily tracking cycles: 07:00, 12:00, 16:00 COT = 12:00, 17:00, 21:00 UTC
    for h in [12, 17, 21]:
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if t <= now:
            t += dt.timedelta(days=1)
        candidates.append(t)

    return min(candidates).isoformat()


def _next_cleanup_run() -> str:
    import datetime as dt

    now = datetime.now(timezone.utc)
    days_until_monday = (7 - now.weekday()) % 7
    monday = now.replace(hour=11, minute=0, second=0, microsecond=0) + dt.timedelta(days=days_until_monday)
    if monday <= now:
        monday += dt.timedelta(days=7)
    return monday.isoformat()


def _next_alert_run() -> str:
    import datetime as dt

    now = datetime.now(timezone.utc)
    minutes_to_next = 30 - (now.minute % 30)
    next_alert = now.replace(second=0, microsecond=0) + dt.timedelta(minutes=minutes_to_next)
    return next_alert.isoformat()
