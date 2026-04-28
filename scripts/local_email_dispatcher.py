"""
Envio local de reportes pendientes por Outlook.

GitHub Actions consulta TCC y guarda el PDF en la BD. Este worker corre en
Windows Task Scheduler y solo envia por Outlook los PDFs pendientes, evitando
repetir la consulta a TCC cuando GitHub ya hizo el ciclo.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.jobs.tracking_job import job_daily_cycle, job_weekly_report
from app.models.report_file import ReportFile
from app.services.email_service import (
    DAILY_RECIPIENTS,
    WEEKLY_RECIPIENTS,
    body_daily_report,
    body_weekly_report,
    send_email,
)
from app.core.config import get_settings
from app.utils.date_utils import utcnow

logger = get_logger(__name__)
settings = get_settings()
BOGOTA_TZ = ZoneInfo("America/Bogota")


async def send_pending_daily(cycle_label: str, *, fallback_run: bool = True) -> bool:
    now = datetime.now(BOGOTA_TZ)
    cutoff = utcnow() - timedelta(hours=12)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReportFile)
            .where(
                ReportFile.report_type == "daily",
                ReportFile.format == "pdf",
                ReportFile.cycle_label == cycle_label,
                ReportFile.email_sent == False,  # noqa: E712
                ReportFile.content_b64.is_not(None),
                ReportFile.generated_at >= cutoff,
            )
            .order_by(ReportFile.generated_at.desc())
            .limit(1)
        )
        report = result.scalar_one_or_none()

        if report is None:
            logger.warning("pending_daily_report_not_found", cycle=cycle_label)
            sent_result = await session.execute(
                select(ReportFile)
                .where(
                    ReportFile.report_type == "daily",
                    ReportFile.format == "pdf",
                    ReportFile.cycle_label == cycle_label,
                    ReportFile.email_sent == True,  # noqa: E712
                    ReportFile.generated_at >= cutoff,
                )
                .order_by(ReportFile.generated_at.desc())
                .limit(1)
            )
            sent_report = sent_result.scalar_one_or_none()
            if sent_report is not None:
                logger.info(
                    "daily_report_already_sent",
                    cycle=cycle_label,
                    filename=sent_report.filename,
                )
                return True
        else:
            pdf_path = settings.reports_daily_path / report.filename
            pdf_path.write_bytes(base64.b64decode(report.content_b64 or ""))
            html = body_daily_report(now.strftime("%d/%m/%Y"), cycle_label)
            sent = await send_email(
                to=DAILY_RECIPIENTS,
                subject="Seguimiento TCC",
                body_html=html,
                attachments=[pdf_path],
            )
            if sent:
                report.email_sent = True
                report.email_sent_at = utcnow()
                await session.commit()
                logger.info("pending_daily_report_sent", cycle=cycle_label, filename=report.filename)
                return True

            await session.rollback()
            logger.error("pending_daily_report_send_failed", cycle=cycle_label, filename=report.filename)
            return False

    if fallback_run:
        logger.warning("pending_daily_report_fallback_full_cycle", cycle=cycle_label)
        await job_daily_cycle(cycle_label)
        return True

    return False


async def send_pending_weekly(*, fallback_run: bool = True) -> bool:
    cutoff = utcnow() - timedelta(days=10)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReportFile)
            .where(
                ReportFile.report_type == "weekly",
                ReportFile.format == "pdf",
                ReportFile.email_sent == False,  # noqa: E712
                ReportFile.content_b64.is_not(None),
                ReportFile.generated_at >= cutoff,
            )
            .order_by(ReportFile.generated_at.desc())
            .limit(1)
        )
        report = result.scalar_one_or_none()

        if report is None:
            logger.warning("pending_weekly_report_not_found")
            sent_result = await session.execute(
                select(ReportFile)
                .where(
                    ReportFile.report_type == "weekly",
                    ReportFile.format == "pdf",
                    ReportFile.email_sent == True,  # noqa: E712
                    ReportFile.generated_at >= cutoff,
                )
                .order_by(ReportFile.generated_at.desc())
                .limit(1)
            )
            sent_report = sent_result.scalar_one_or_none()
            if sent_report is not None:
                logger.info("weekly_report_already_sent", filename=sent_report.filename)
                return True
        else:
            pdf_path = settings.reports_weekly_path / report.filename
            pdf_path.write_bytes(base64.b64decode(report.content_b64 or ""))
            week_start = report.week_start.isoformat() if report.week_start else ""
            week_end = report.week_end.isoformat() if report.week_end else ""
            html = body_weekly_report(week_start, week_end)
            sent = await send_email(
                to=WEEKLY_RECIPIENTS,
                subject="Seguimiento TCC",
                body_html=html,
                attachments=[pdf_path],
            )
            if sent:
                report.email_sent = True
                report.email_sent_at = utcnow()
                await session.commit()
                logger.info("pending_weekly_report_sent", filename=report.filename)
                return True

            await session.rollback()
            logger.error("pending_weekly_report_send_failed", filename=report.filename)
            return False

    if fallback_run:
        logger.warning("pending_weekly_report_fallback_full_cycle")
        await job_weekly_report()
        return True

    return False
