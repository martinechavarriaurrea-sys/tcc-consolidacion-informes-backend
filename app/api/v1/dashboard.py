import datetime as dt
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.api.deps import get_report_service
from app.models.shipment import Shipment
from app.schemas.dashboard import DashboardSummary
from app.services.report_service import ReportService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

_SISTEMA_INACTIVO_HORAS = 6


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(svc: ReportService = Depends(get_report_service)):
    return await svc.get_dashboard_summary()


@router.get("/stats")
async def get_stats(svc: ReportService = Depends(get_report_service)):
    s = await svc.get_dashboard_summary()

    # Active shipments list for live dashboard
    result = await svc.session.execute(
        select(Shipment)
        .where(Shipment.is_active == True)  # noqa: E712
        .order_by(Shipment.current_status_at.desc().nulls_last())
    )
    active_shipments = result.scalars().all()

    guias_activas = [
        {
            "numero_guia": ship.tracking_number,
            "estado_actual": ship.current_status or "desconocido",
            "ultima_actualizacion": ship.current_status_at.isoformat() if ship.current_status_at else None,
        }
        for ship in active_shipments
    ]

    ultima_ejecucion = s.last_tracking_run
    if ultima_ejecucion and ultima_ejecucion.tzinfo is None:
        ultima_ejecucion = ultima_ejecucion.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    sistema_activo = (
        ultima_ejecucion is not None
        and (now - ultima_ejecucion).total_seconds() < _SISTEMA_INACTIVO_HORAS * 3600
    )

    proxima_ejecucion = _next_report_run()

    return {
        # KPIs
        "total_activas": s.total_active,
        "total_guias_activas": s.total_active,
        "total_entregadas": s.total_delivered_today,
        "total_guias_entregadas_hoy": s.total_delivered_today,
        "con_novedad": s.total_with_issues,
        "sin_movimiento": s.total_no_movement_72h,
        "monitoreadas_hoy": s.total_active,
        # Timing
        "ultima_ejecucion": ultima_ejecucion.isoformat() if ultima_ejecucion else None,
        "proxima_ejecucion": proxima_ejecucion,
        "proxima_reporte": proxima_ejecucion,
        # Live shipments
        "guias_activas": guias_activas,
        # System health
        "sistema_activo": sistema_activo,
        "estado_automatizacion": "ejecutado" if s.last_tracking_run else "programado",
    }


def _next_report_run() -> str:
    now = datetime.now(timezone.utc)
    candidates: list[datetime] = []

    # Tracking cycles: 07:00, 12:00, 16:00 COT = 12:00, 17:00, 21:00 UTC
    for h in [12, 17, 21]:
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if t <= now:
            t += dt.timedelta(days=1)
        candidates.append(t)

    return min(candidates).isoformat()
