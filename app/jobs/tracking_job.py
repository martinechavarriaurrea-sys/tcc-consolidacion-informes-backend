"""
Jobs programados del sistema TCC.

CICLO DIARIO (job_daily_cycle)
────────────────────────────────────────────────────────────────────────────
1. Obtiene guías activas de BD
2. Consulta TCC por cada una (TrackingService.run_full)
3. Las entregadas en este ciclo quedan is_active=False → aparecen en el
   informe de este ciclo pero NO en el siguiente (regla de negocio)
4. Recolecta datos para el reporte
5. Genera Excel + PDF
6. Guarda metadata de archivos en report_files
7. Envía correo con ambos adjuntos a destinatarios diarios

CONSOLIDADO SEMANAL (job_weekly_report)
────────────────────────────────────────────────────────────────────────────
Se ejecuta los lunes 07:00. Genera un consolidado de la semana ANTERIOR
(lunes–domingo precedente). Incluye guías activas durante ese período y
las entregadas en él. Genera Excel + PDF y envía a destinatarios semanales.

ALERTAS 72 HORAS (job_check_alerts)
────────────────────────────────────────────────────────────────────────────
Corre cada 30 min. Detecta guías sin movimiento ≥72h. Solo genera alerta
nueva si no hay una alerta abierta del mismo tipo para esa guía (anti-spam).
Cuando la guía tiene movimiento, la alerta se marca resuelta.
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.report_file import ReportFile
from app.models.shipment import Shipment
from app.models.tracking_event import ShipmentTrackingEvent
from app.repositories.shipment_repository import ShipmentRepository
from app.repositories.tracking_event_repository import TrackingEventRepository
from app.services.alert_service import AlertService
from app.services.email_service import (
    ALERT_RECIPIENTS,
    DAILY_RECIPIENTS,
    WEEKLY_RECIPIENTS,
    body_alert_72h,
    body_daily_report,
    body_weekly_report,
    send_email,
)
from app.services.excel_service import DailyReportRow, ExcelService, WeeklyReportRow
from app.services.pdf_service import PdfService
from app.services.report_service import ReportService
from app.services.tracking_service import TrackingService
from app.utils.date_utils import count_days_excluding_sundays, hours_since, utcnow, week_boundaries

logger = get_logger(__name__)
settings = get_settings()

_excel_svc = ExcelService()
_pdf_svc = PdfService()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bogota_now() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Bogota"))


async def _save_report_file(
    session: AsyncSession,
    *,
    report_type: str,
    fmt: str,
    filename: str,
    file_path: str,
    cycle_label: str | None = None,
    week_start: date | None = None,
    week_end: date | None = None,
    email_sent: bool = False,
    email_sent_at: datetime | None = None,
) -> ReportFile:
    import os
    size = os.path.getsize(file_path) if os.path.exists(file_path) else None
    rf = ReportFile(
        report_type=report_type,
        format=fmt,
        filename=filename,
        file_path=file_path,
        file_size_bytes=size,
        cycle_label=cycle_label,
        week_start=week_start,
        week_end=week_end,
        generated_at=utcnow(),
        email_sent=email_sent,
        email_sent_at=email_sent_at,
    )
    session.add(rf)
    await session.flush()
    return rf


async def _collect_daily_rows(
    session: AsyncSession,
    cycle_ts: datetime,
    cycle_started_at: datetime | None = None,
) -> list[DailyReportRow]:
    """
    Recolecta filas para el reporte diario:
    - Guías activas en este momento
    - Guías entregadas HOY (delivered_at >= medianoche UTC)
    """
    cycle_window_start = cycle_started_at or cycle_ts.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    cycle_date = cycle_ts.date()
    cycle_time_str = cycle_ts.strftime("%H:%M")

    # Guías activas
    active_result = await session.execute(
        select(Shipment).where(Shipment.is_active == True)  # noqa: E712
    )
    active_shipments = list(active_result.scalars().all())

    # Guías entregadas hoy (ya inactivas tras este ciclo)
    delivered_today_result = await session.execute(
        select(Shipment).where(
            Shipment.is_active == False,  # noqa: E712
            Shipment.delivered_at.is_not(None),
            Shipment.updated_at >= cycle_window_start,
        )
    )
    delivered_today = list(delivered_today_result.scalars().all())

    all_shipments = {s.id: s for s in active_shipments}
    for s in delivered_today:
        all_shipments[s.id] = s  # puede solapar si se procesó en este ciclo

    event_repo = TrackingEventRepository(session)
    rows: list[DailyReportRow] = []

    for shipment in all_shipments.values():
        ref_dt = shipment.current_status_at or shipment.first_seen_at
        hrs = hours_since(ref_dt) if ref_dt else None
        days = hrs / 24 if hrs is not None else None
        is_alert = hrs is not None and hrs >= settings.alert_no_movement_hours and shipment.is_active

        obs_parts = []
        if is_alert:
            obs_parts.append(f"Sin movimiento {hrs:.0f}h")
        if shipment.current_status == "novedad":
            obs_parts.append("Con novedad")
        if not shipment.is_active and shipment.delivered_at:
            obs_parts.append("Entregado en este ciclo")

        # Dias en transito desde fecha de despacho (sin contar domingos)
        days_in_transit = None
        if shipment.shipping_date:
            end = (shipment.delivered_at.date() if shipment.delivered_at
                   else cycle_date)
            days_in_transit = count_days_excluding_sundays(shipment.shipping_date, end)

        rows.append(DailyReportRow(
            query_date=cycle_date,
            query_time=cycle_time_str,
            tracking_number=shipment.tracking_number,
            advisor_name=shipment.advisor_name,
            client_name=shipment.client_name or "",
            current_status=shipment.current_status,
            current_status_raw=shipment.current_status_raw or "",
            last_event_at=shipment.current_status_at,
            hours_without_movement=round(hrs, 1) if hrs is not None else None,
            days_without_movement=round(days, 2) if days is not None else None,
            is_delivered=not shipment.is_active and bool(shipment.delivered_at),
            is_alert=is_alert,
            observations="; ".join(obs_parts),
            shipping_date=shipment.shipping_date,
            days_in_transit=days_in_transit,
        ))

    # Ordena: primero alertas, luego activas, luego entregadas
    rows.sort(key=lambda r: (not r.is_alert, r.is_delivered, r.tracking_number))
    return rows


async def _collect_weekly_rows(
    session: AsyncSession,
    week_start: date,
    week_end: date,
) -> list[WeeklyReportRow]:
    """Recolecta filas para el consolidado semanal de la semana indicada."""
    week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    week_end_dt = datetime.combine(week_end, datetime.max.time()).replace(tzinfo=timezone.utc)

    # Guías con actividad en la semana (activas durante el período)
    result = await session.execute(
        select(Shipment).where(
            Shipment.first_seen_at <= week_end_dt,
            (Shipment.closed_at.is_(None)) | (Shipment.closed_at >= week_start_dt),
        )
    )
    shipments = list(result.scalars().all())

    week_label = f"{week_start.strftime('%Y-%m-%d')} al {week_end.strftime('%Y-%m-%d')}"
    rows: list[WeeklyReportRow] = []

    for shipment in shipments:
        # Eventos en la semana
        events_result = await session.execute(
            select(ShipmentTrackingEvent)
            .where(
                ShipmentTrackingEvent.shipment_id == shipment.id,
                ShipmentTrackingEvent.event_at >= week_start_dt,
                ShipmentTrackingEvent.event_at <= week_end_dt,
            )
            .order_by(ShipmentTrackingEvent.event_at)
        )
        events = list(events_result.scalars().all())

        first_status = events[0].status_normalized if events else shipment.current_status
        last_status = events[-1].status_normalized if events else shipment.current_status
        total_movements = len(events)

        # Alertas en la semana
        from sqlalchemy import func
        from app.models.alert_event import AlertEvent
        alert_result = await session.execute(
            select(func.count(AlertEvent.id)).where(
                AlertEvent.shipment_id == shipment.id,
                AlertEvent.triggered_at >= week_start_dt,
                AlertEvent.triggered_at <= week_end_dt,
            )
        )
        alert_count = alert_result.scalar_one() or 0

        obs_parts = []
        if alert_count > 0:
            obs_parts.append(f"Alertas: {alert_count}")
        if shipment.current_status == "novedad":
            obs_parts.append("Con novedad")

        rows.append(WeeklyReportRow(
            week_label=week_label,
            tracking_number=shipment.tracking_number,
            advisor_name=shipment.advisor_name,
            client_name=shipment.client_name or "",
            first_status=first_status,
            last_status=last_status,
            delivered_at=shipment.delivered_at,
            total_movements=total_movements,
            still_active=shipment.is_active,
            alerts_detected=alert_count,
            observations="; ".join(obs_parts),
        ))

    rows.sort(key=lambda r: (r.still_active, r.tracking_number))
    return rows


async def _generate_daily_report(
    session: AsyncSession,
    *,
    cycle_label: str,
    cycle_ts: datetime,
    cycle_started_at: datetime | None,
) -> "Path | None":
    """Genera archivos Excel+PDF del ciclo. Retorna ruta del PDF o None si no hay datos."""
    from pathlib import Path as _Path
    report_date = cycle_ts.date()
    rows = await _collect_daily_rows(session, cycle_ts, cycle_started_at)
    logger.info("job_daily_cycle_rows", count=len(rows))

    if not rows:
        logger.info("job_daily_cycle_no_rows", cycle=cycle_label)
        return None

    ts_str = report_date.strftime("%Y-%m-%d")
    base_name = f"reporte_tcc_diario_{ts_str}_{cycle_label}"
    xlsx_path = settings.reports_daily_path / f"{base_name}.xlsx"
    pdf_path = settings.reports_daily_path / f"{base_name}.pdf"

    _excel_svc.generate_daily(rows, xlsx_path, cycle_label, report_date)
    _pdf_svc.generate_daily(rows, pdf_path, cycle_label, report_date, utcnow())
    logger.info("job_daily_cycle_files_generated", xlsx=str(xlsx_path), pdf=str(pdf_path))

    await _save_report_file(session, report_type="daily", fmt="xlsx",
                            filename=xlsx_path.name, file_path=str(xlsx_path), cycle_label=cycle_label)
    await _save_report_file(session, report_type="daily", fmt="pdf",
                            filename=pdf_path.name, file_path=str(pdf_path), cycle_label=cycle_label)

    return pdf_path


async def job_daily_report_only(cycle_label: str, cycle_started_at: datetime | None = None) -> "Path | None":
    """Genera el reporte diario sin consultar TCC ni enviar email. Retorna ruta del PDF."""
    cycle_ts = _bogota_now()
    async with AsyncSessionLocal() as session:
        try:
            pdf_path = await _generate_daily_report(
                session,
                cycle_label=cycle_label,
                cycle_ts=cycle_ts,
                cycle_started_at=cycle_started_at,
            )
            await session.commit()
            return pdf_path
        except Exception:
            logger.exception("job_daily_report_only_error", cycle=cycle_label)
            await session.rollback()
            raise


# ── Job: ciclo diario ─────────────────────────────────────────────────────────

async def job_daily_cycle(cycle_label: str) -> None:
    """
    Ciclo completo: consulta TCC → reporte Excel+PDF → email.
    cycle_label: "0700" | "1200" | "1600"
    """
    logger.info("job_daily_cycle_start", cycle=cycle_label)
    cycle_ts = _bogota_now()

    async with AsyncSessionLocal() as session:
        try:
            # 1. Consulta TCC
            tracking_svc = TrackingService(session)
            run = await tracking_svc.run_full(run_type=f"scheduled_{cycle_label}")
            await session.commit()
            logger.info("job_daily_cycle_tracking_done", run_id=run.id, status=run.status)
            pdf_path = await _generate_daily_report(
                session,
                cycle_label=cycle_label,
                cycle_ts=cycle_ts,
                cycle_started_at=run.started_at,
            )
            if pdf_path:
                html = body_daily_report(cycle_ts.strftime("%d/%m/%Y"), cycle_label)
                sent = await send_email(to=DAILY_RECIPIENTS, subject="Seguimiento TCC",
                                        body_html=html, attachments=[pdf_path])
                logger.info("job_daily_report_generated", cycle=cycle_label, email_sent=sent)
            await session.commit()
            logger.info("job_daily_cycle_done", cycle=cycle_label, report_generated=pdf_path is not None)
            return

            # 2. Recolecta datos del ciclo (incluye las recién entregadas)
            rows = await _collect_daily_rows(session, cycle_ts, run.started_at)
            logger.info("job_daily_cycle_rows", count=len(rows))

            if not rows:
                logger.info("job_daily_cycle_no_rows", cycle=cycle_label)
                return

            # 3. Genera archivos
            ts_str = report_date.strftime("%Y-%m-%d")
            base_name = f"reporte_tcc_diario_{ts_str}_{cycle_label}"
            xlsx_path = settings.reports_daily_path / f"{base_name}.xlsx"
            pdf_path = settings.reports_daily_path / f"{base_name}.pdf"

            _excel_svc.generate_daily(rows, xlsx_path, cycle_label, report_date)
            _pdf_svc.generate_daily(rows, pdf_path, cycle_label, report_date, utcnow())
            logger.info("job_daily_cycle_files_generated", xlsx=str(xlsx_path), pdf=str(pdf_path))

            # 4. Registra archivos en BD
            daily_files = [
                await _save_report_file(
                    session,
                    report_type="daily",
                    fmt="xlsx",
                    filename=xlsx_path.name,
                    file_path=str(xlsx_path),
                    cycle_label=cycle_label,
                ),
                await _save_report_file(
                    session,
                    report_type="daily",
                    fmt="pdf",
                    filename=pdf_path.name,
                    file_path=str(pdf_path),
                    cycle_label=cycle_label,
                ),
            ]

            # 5. Envía correo
            subject = "Seguimiento TCC"
            html = body_daily_report(report_date.strftime("%d/%m/%Y"), cycle_label)
            sent = await send_email(
                to=DAILY_RECIPIENTS,
                subject=subject,
                body_html=html,
                attachments=[pdf_path],
            )

            # 6. Actualiza estado de envío en BD
            if sent:
                now = utcnow()
                for report_file in daily_files:
                    report_file.email_sent = True
                    report_file.email_sent_at = now
            await session.commit()
            logger.info("job_daily_cycle_done", cycle=cycle_label, email_sent=sent)

        except Exception:
            logger.exception("job_daily_cycle_error", cycle=cycle_label)
            await session.rollback()
            raise


# ── Job: consolidado semanal ──────────────────────────────────────────────────

async def job_weekly_report() -> None:
    """
    Genera el consolidado de la semana anterior (lunes a lunes).
    Se ejecuta los lunes 07:00: cubre desde el lunes anterior hasta hoy (lunes inclusive).
    """
    logger.info("job_weekly_report_start")
    now = _bogota_now()

    # Período: lunes pasado (hace 7 días) hasta hoy (lunes actual)
    week_start = now.date() - timedelta(days=7)
    week_end = now.date()
    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as session:
        try:
            report_svc = ReportService(session)
            rollup = await report_svc.generate_weekly_rollup(reference=week_start)
            await session.commit()
            logger.info("job_weekly_rollup_created", id=rollup.id, week_start=week_start_str)

            rows = await _collect_weekly_rows(session, week_start, week_end)
            logger.info("job_weekly_report_rows", count=len(rows))

            base_name = f"reporte_tcc_semanal_{week_start_str}_al_{week_end_str}"
            xlsx_path = settings.reports_weekly_path / f"{base_name}.xlsx"
            pdf_path = settings.reports_weekly_path / f"{base_name}.pdf"

            _excel_svc.generate_weekly(rows, week_start, week_end, xlsx_path)
            _pdf_svc.generate_weekly(rows, week_start, week_end, pdf_path, utcnow())
            logger.info("job_weekly_files_generated", xlsx=str(xlsx_path), pdf=str(pdf_path))

            await _save_report_file(session, report_type="weekly", fmt="xlsx",
                                    filename=xlsx_path.name, file_path=str(xlsx_path),
                                    week_start=week_start, week_end=week_end)
            await _save_report_file(session, report_type="weekly", fmt="pdf",
                                    filename=pdf_path.name, file_path=str(pdf_path),
                                    week_start=week_start, week_end=week_end)

            html = body_weekly_report(week_start_str, week_end_str)
            sent = await send_email(to=WEEKLY_RECIPIENTS, subject="Seguimiento TCC",
                                    body_html=html, attachments=[pdf_path])

            await session.commit()
            logger.info("job_weekly_report_done", email_sent=sent, week_start=week_start_str,
                        pdf=str(pdf_path))

        except Exception:
            logger.exception("job_weekly_report_error")
            await session.rollback()
            raise


# ── Jobs para GitHub Actions (sin envío de email) ────────────────────────────

async def job_weekly_report_pdf() -> "tuple[Path, str, str] | None":
    """Genera el PDF semanal sin enviar email. Retorna (pdf_path, week_start_str, week_end_str)."""
    now = _bogota_now()
    week_start = now.date() - timedelta(days=7)
    week_end = now.date()
    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    async with AsyncSessionLocal() as session:
        try:
            report_svc = ReportService(session)
            await report_svc.generate_weekly_rollup(reference=week_start)
            await session.commit()

            rows = await _collect_weekly_rows(session, week_start, week_end)
            if not rows:
                return None

            base_name = f"reporte_tcc_semanal_{week_start_str}_al_{week_end_str}"
            pdf_path = settings.reports_weekly_path / f"{base_name}.pdf"
            xlsx_path = settings.reports_weekly_path / f"{base_name}.xlsx"
            _excel_svc.generate_weekly(rows, week_start, week_end, xlsx_path)
            _pdf_svc.generate_weekly(rows, week_start, week_end, pdf_path, utcnow())

            await _save_report_file(session, report_type="weekly", fmt="pdf",
                                    filename=pdf_path.name, file_path=str(pdf_path),
                                    week_start=week_start, week_end=week_end)
            await session.commit()
            logger.info("job_weekly_report_pdf_done", pdf=str(pdf_path))
            return pdf_path, week_start_str, week_end_str
        except Exception:
            logger.exception("job_weekly_report_pdf_error")
            await session.rollback()
            raise


async def job_check_alerts_data() -> list[dict]:
    """Detecta alertas 72h, las registra en BD y retorna info para envío externo."""
    async with AsyncSessionLocal() as session:
        try:
            alert_svc = AlertService(session)
            new_alerts = await alert_svc.check_all()
            await session.commit()

            if not new_alerts:
                return []

            stale = await alert_svc.get_shipments_without_movement()
            return [
                {
                    "tracking_number": s.tracking_number,
                    "advisor_name": s.advisor_name,
                    "current_status": s.current_status,
                    "hours": f"{hours_since(s.current_status_at or s.first_seen_at):.0f}",
                }
                for s in stale
            ]
        except Exception:
            logger.exception("job_check_alerts_data_error")
            await session.rollback()
            raise


# ── Job: verificación de alertas 72h ─────────────────────────────────────────

async def job_check_alerts() -> None:
    """
    Detecta guías sin movimiento ≥72h y envía alerta por correo.

    POLÍTICA ANTI-SPAM
    ─────────────────────────────────────────────────────────────────────────
    - Solo se crea un AlertEvent por guía cuando no hay alerta abierta.
    - Si la guía vuelve a tener movimiento, la alerta se marca resuelta.
    - No se envía correo si no hay alertas NUEVAS en este ciclo.
    - Referencia en código: AlertService._check_no_movement()
    """
    logger.info("job_check_alerts_start")

    async with AsyncSessionLocal() as session:
        try:
            alert_svc = AlertService(session)
            new_alerts = await alert_svc.check_all()
            await session.commit()

            if new_alerts:
                # Prepara info para el cuerpo del correo
                stale = await alert_svc.get_shipments_without_movement()
                shipments_info = [
                    {
                        "tracking_number": s.tracking_number,
                        "advisor_name": s.advisor_name,
                        "current_status": s.current_status,
                        "hours": f"{hours_since(s.current_status_at or s.first_seen_at):.0f}",
                    }
                    for s in stale
                ]

                subject = "Seguimiento TCC"
                html = body_alert_72h(shipments_info)
                sent = await send_email(
                    to=ALERT_RECIPIENTS,
                    subject=subject,
                    body_html=html,
                )
                logger.info("job_check_alerts_email_sent", new=len(new_alerts), sent=sent)
            else:
                logger.info("job_check_alerts_no_new_alerts")

            logger.info("job_check_alerts_done", new=len(new_alerts))

        except Exception:
            logger.exception("job_check_alerts_error")
            await session.rollback()
            raise


# ── Job: limpieza de guías entregadas hace más de 14 días ─────────────────────

async def job_cleanup_old_guias() -> None:
    """Elimina guías entregadas o cerradas hace más de 14 días."""
    from datetime import timedelta
    from sqlalchemy import delete
    from app.models.tracking_event import ShipmentTrackingEvent
    from app.models.alert_event import AlertEvent

    logger.info("job_cleanup_start")
    cutoff = utcnow() - timedelta(days=14)

    async with AsyncSessionLocal() as session:
        try:
            old = await session.execute(
                select(Shipment).where(
                    Shipment.is_active == False,
                    (Shipment.delivered_at <= cutoff) | (Shipment.closed_at <= cutoff),
                )
            )
            to_delete = old.scalars().all()
            count = len(to_delete)
            for s in to_delete:
                await session.execute(delete(AlertEvent).where(AlertEvent.shipment_id == s.id))
                await session.execute(delete(ShipmentTrackingEvent).where(ShipmentTrackingEvent.shipment_id == s.id))
                await session.delete(s)
            await session.commit()
            logger.info("job_cleanup_done", deleted=count)
        except Exception:
            await session.rollback()
            logger.exception("job_cleanup_error")
