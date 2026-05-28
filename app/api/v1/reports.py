"""
Endpoints de exportación manual de reportes.

GET  /api/v1/reports/daily        → genera y devuelve reporte diario en xlsx o pdf
GET  /api/v1/reports/weekly       → genera y devuelve consolidado semanal en xlsx o pdf
POST /api/v1/reports/trigger/{job} → dispara un job manualmente (daily/weekly/alerts)
GET  /api/v1/reports/history      → lista los reportes generados (metadata BD)
"""

import asyncio
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import get_settings
from app.core.logging import get_logger
from app.jobs.tracking_job import (
    _bogota_now,
    _collect_daily_rows,
    _collect_weekly_rows,
    job_check_alerts,
    job_daily_cycle,
    job_weekly_report,
    _excel_svc,
    _pdf_svc,
    DailyReportRow,
)
from app.models.report_file import ReportFile
from app.utils.date_utils import utcnow, week_boundaries
from app.utils.status_normalizer import effective_status

router = APIRouter(prefix="/reports", tags=["reports"])
logger = get_logger(__name__)
settings = get_settings()

FormatParam = Literal["xlsx", "pdf"]


@router.get("/daily")
async def export_daily_report(
    report_date: date | None = Query(default=None, description="Fecha del reporte (YYYY-MM-DD), default hoy"),
    format: FormatParam = Query(default="xlsx"),
    session: AsyncSession = Depends(get_db),
):
    """
    Genera y descarga el reporte diario al momento actual (exportación manual).
    No envía correo. Solo genera el archivo y lo retorna.
    """
    now = _bogota_now()
    target_date = report_date or now.date()
    cycle_label = now.strftime("%H%M")
    cycle_time_str = now.strftime("%H:%M")

    rows = await _collect_daily_rows(session, now)
    if not rows:
        raise HTTPException(status_code=404, detail="No hay guías activas para reportar.")

    ts_str = target_date.strftime("%Y-%m-%d")
    base_name = f"reporte_tcc_diario_{ts_str}_{cycle_label}_manual"
    out_dir = settings.reports_daily_path

    if format == "xlsx":
        path = out_dir / f"{base_name}.xlsx"
        _excel_svc.generate_daily(rows, path, cycle_label, target_date)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        path = out_dir / f"{base_name}.pdf"
        _pdf_svc.generate_daily(rows, path, cycle_label, target_date, utcnow())
        media_type = "application/pdf"

    logger.info("manual_export_daily", format=format, path=str(path), rows=len(rows))
    return FileResponse(path=str(path), filename=path.name, media_type=media_type)


@router.get("/weekly")
async def export_weekly_report(
    week_of: date | None = Query(
        default=None,
        description="Cualquier fecha de la semana a reportar (YYYY-MM-DD), default semana anterior",
    ),
    format: FormatParam = Query(default="xlsx"),
    session: AsyncSession = Depends(get_db),
):
    """
    Genera y descarga el consolidado semanal para la semana indicada (exportación manual).
    """
    from datetime import timedelta
    now = _bogota_now()
    reference = week_of or (now.date() - timedelta(days=7))
    week_start, week_end = week_boundaries(reference)

    rows = await _collect_weekly_rows(session, week_start, week_end)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No hay guías para la semana {week_start} al {week_end}.",
        )

    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")
    base_name = f"reporte_tcc_semanal_{week_start_str}_al_{week_end_str}_manual"
    out_dir = settings.reports_weekly_path

    if format == "xlsx":
        path = out_dir / f"{base_name}.xlsx"
        _excel_svc.generate_weekly(rows, week_start, week_end, path)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        path = out_dir / f"{base_name}.pdf"
        _pdf_svc.generate_weekly(rows, week_start, week_end, path, utcnow())
        media_type = "application/pdf"

    logger.info("manual_export_weekly", format=format, path=str(path), rows=len(rows))
    return FileResponse(path=str(path), filename=path.name, media_type=media_type)


@router.get("/range")
async def export_range_report(
    fecha_inicio: date = Query(..., description="Fecha inicio YYYY-MM-DD"),
    fecha_fin: date = Query(..., description="Fecha fin YYYY-MM-DD"),
    format: FormatParam = Query(default="pdf"),
    session: AsyncSession = Depends(get_db),
):
    """Genera PDF o Excel con todas las guías que tuvieron actividad en el rango indicado."""
    from datetime import datetime
    from app.models.shipment import Shipment
    from sqlalchemy import or_
    from app.utils.date_utils import count_days_excluding_sundays

    if fecha_fin < fecha_inicio:
        raise HTTPException(status_code=400, detail="fecha_fin debe ser mayor o igual a fecha_inicio.")
    if (fecha_fin - fecha_inicio).days > 365:
        raise HTTPException(status_code=400, detail="El rango no puede superar 365 días.")

    from datetime import timezone as _tz
    inicio_dt = datetime.combine(fecha_inicio, datetime.min.time()).replace(tzinfo=_tz.utc)
    fin_dt    = datetime.combine(fecha_fin,    datetime.max.time()).replace(tzinfo=_tz.utc)

    # Guías que estuvieron activas durante el rango:
    # se registraron antes del fin del rango
    # Y (siguen activas  O  se cerraron/entregaron después del inicio del rango)
    result = await session.execute(
        select(Shipment).where(
            Shipment.first_seen_at <= fin_dt,
            or_(
                Shipment.is_active == True,          # noqa: E712
                Shipment.delivered_at >= inicio_dt,
                Shipment.closed_at    >= inicio_dt,
            )
        ).order_by(Shipment.advisor_name, Shipment.client_name)
    )
    shipments = result.scalars().all()

    if not shipments:
        raise HTTPException(
            status_code=404,
            detail=f"No hay guías para el rango {fecha_inicio} al {fecha_fin}.",
        )

    now = utcnow()
    rows = []
    for s in shipments:
        hrs = None
        if s.current_status_at:
            sat = s.current_status_at.replace(tzinfo=None) if s.current_status_at.tzinfo else s.current_status_at
            now_naive = now.replace(tzinfo=None) if now.tzinfo else now
            delta = now_naive - sat
            hrs = delta.total_seconds() / 3600

        shipping_date = s.shipping_date
        if isinstance(shipping_date, datetime):
            shipping_date = shipping_date.date()

        days_in_transit = None
        if isinstance(shipping_date, date):
            end = s.delivered_at.date() if s.delivered_at else fecha_fin
            days_in_transit = count_days_excluding_sundays(shipping_date, end)

        rows.append(DailyReportRow(
            query_date=fecha_inicio,
            query_time=now.strftime("%H:%M"),
            tracking_number=s.tracking_number,
            advisor_name=s.advisor_name,
            client_name=s.client_name or "",
            current_status=effective_status(s.current_status, s.current_status_raw, default=""),
            current_status_raw=s.current_status_raw or "",
            last_event_at=s.current_status_at,
            hours_without_movement=round(hrs, 1) if hrs else None,
            days_without_movement=round(hrs / 24, 1) if hrs else None,
            is_delivered=bool(s.delivered_at),
            is_alert=False,
            observations="",
            shipping_date=shipping_date if isinstance(shipping_date, date) else None,
            days_in_transit=days_in_transit,
        ))

    inicio_str = fecha_inicio.strftime("%Y-%m-%d")
    fin_str    = fecha_fin.strftime("%Y-%m-%d")
    cycle_label = now.strftime("%H%M")

    if format == "xlsx":
        filename = f"informe_tcc_{inicio_str}_al_{fin_str}.xlsx"
        path = settings.reports_daily_path / filename
        _excel_svc.generate_daily(rows, path, cycle_label, fecha_inicio)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        filename = f"informe_tcc_{inicio_str}_al_{fin_str}.pdf"
        path = settings.reports_daily_path / filename
        _pdf_svc.generate_range(rows, fecha_inicio, fecha_fin, path, now)
        media_type = "application/pdf"

    logger.info("range_report_generated", format=format, rows=len(rows), inicio=inicio_str, fin=fin_str)
    return FileResponse(path=str(path), filename=filename, media_type=media_type)


@router.post("/trigger/{job_name}")
async def trigger_job(
    job_name: Literal["daily_0700", "daily_1200", "daily_1600", "weekly", "alerts"],
):
    """
    Dispara un job manualmente de forma asíncrona.
    El resultado se refleja en los logs y en la BD.
    Retorna inmediatamente con status 202.
    """
    async def _run():
        try:
            if job_name == "daily_0700":
                await job_daily_cycle("0700")
            elif job_name == "daily_1200":
                await job_daily_cycle("1200")
            elif job_name == "daily_1600":
                await job_daily_cycle("1600")
            elif job_name == "weekly":
                await job_weekly_report()
            elif job_name == "alerts":
                await job_check_alerts()
        except Exception:
            logger.exception("manual_trigger_error", job=job_name)

    asyncio.create_task(_run())
    logger.info("manual_trigger_dispatched", job=job_name)
    return {"status": "dispatched", "job": job_name, "note": "El job corre en background; revisa los logs."}


@router.get("/history")
async def report_history(
    report_type: str | None = Query(default=None, description="daily | weekly | alert"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
):
    """Lista el historial de reportes generados (metadata de BD)."""
    stmt = select(ReportFile).order_by(ReportFile.generated_at.desc()).limit(limit)
    if report_type:
        stmt = stmt.where(ReportFile.report_type == report_type)
    result = await session.execute(stmt)
    files = result.scalars().all()

    return [
        {
            "id": f.id,
            "report_type": f.report_type,
            "format": f.format,
            "filename": f.filename,
            "file_size_bytes": f.file_size_bytes,
            "cycle_label": f.cycle_label,
            "week_start": str(f.week_start) if f.week_start else None,
            "week_end": str(f.week_end) if f.week_end else None,
            "generated_at": f.generated_at.isoformat(),
            "email_sent": f.email_sent,
            "email_sent_at": f.email_sent_at.isoformat() if f.email_sent_at else None,
        }
        for f in files
    ]

