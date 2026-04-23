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
    proxima = _next_run()
    return {
        "total_activas": s.total_active,
        "total_entregadas": s.total_delivered_today,
        "con_novedad": s.total_with_issues,
        "sin_movimiento": s.total_no_movement_72h,
        "monitoreadas_hoy": s.total_active,
        "ultima_ejecucion": s.last_tracking_run.isoformat() if s.last_tracking_run else None,
        "proxima_ejecucion": proxima,
    }


def _next_run() -> str:
    now = datetime.now(timezone.utc)
    # Horarios en UTC: 07:00, 12:00, 16:00 COT = 12:00, 17:00, 21:00 UTC
    hours = [12, 17, 21]
    for h in hours:
        candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate.isoformat()
    import datetime as dt
    tomorrow = now + dt.timedelta(days=1)
    return tomorrow.replace(hour=12, minute=0, second=0, microsecond=0).isoformat()
