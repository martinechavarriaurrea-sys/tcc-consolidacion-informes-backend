"""
Worker para GitHub Actions.

Consulta TCC desde el runner de GitHub y envia los resultados al backend.
Asi Vercel Hobby solo persiste datos y genera el reporte, sin cargar con el
tiempo de consulta de todas las guias.
"""

from __future__ import annotations

import asyncio
import base64
import os
import smtplib
import ssl
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import configure_logging, get_logger
from app.integrations.tcc.client import get_tcc_client

logger = get_logger(__name__)

MAX_CONCURRENT = int(os.getenv("TCC_WORKER_CONCURRENCY", "5"))
PAGE_SIZE = 200


def _send_smtp_email(
    pdf_bytes: bytes,
    pdf_filename: str,
    subject: str,
    body_html: str,
) -> bool:
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_host = os.getenv("SMTP_HOST", "smtp.office365.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    recipient = os.getenv("EMAIL_RECIPIENT", "echavarriam@asteco.com.co")

    if not smtp_user or not smtp_password:
        logger.warning("github_worker_smtp_not_configured")
        return False

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    part = MIMEApplication(pdf_bytes, Name=pdf_filename)
    part["Content-Disposition"] = f'attachment; filename="{pdf_filename}"'
    msg.attach(part)

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(smtp_user, [recipient], msg.as_bytes())

    logger.info("github_worker_email_sent", recipient=recipient, host=smtp_host, filename=pdf_filename)
    return True


def _daily_body_html(report_date: str, cycle_label: str) -> str:
    cycle_display = f"{cycle_label[:2]}:{cycle_label[2:]}"
    return f"""<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px">
<div style="border-left:4px solid #1B3A6B;padding-left:16px;margin-bottom:16px">
  <h2 style="color:#1B3A6B;margin:0">Seguimiento de Guias TCC</h2>
  <p style="color:#666;margin:4px 0">{report_date} - Ciclo {cycle_display}</p>
</div>
<p>Se adjunta el PDF con el detalle del ciclo.</p>
<p style="color:#888;font-size:12px;margin-top:32px">
  Generado automaticamente por el sistema TCC ASTECO.
</p>
</body></html>"""


def _weekly_body_html(period: str) -> str:
    return f"""<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px">
<div style="border-left:4px solid #1B3A6B;padding-left:16px;margin-bottom:16px">
  <h2 style="color:#1B3A6B;margin:0">Seguimiento de Guias TCC - Consolidado Semanal</h2>
  <p style="color:#666;margin:4px 0">Semana: {period}</p>
</div>
<p>Se adjunta el PDF con el consolidado de la semana.</p>
<p style="color:#888;font-size:12px;margin-top:32px">
  Generado automaticamente por el sistema TCC ASTECO.
</p>
</body></html>"""


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


async def _fetch_active_tracking_numbers(client: httpx.AsyncClient, backend_url: str) -> list[str]:
    page = 1
    tracking_numbers: list[str] = []

    while True:
        response = await client.get(
            f"{backend_url}/api/v1/shipments",
            params={"is_active": "true", "page": page, "page_size": PAGE_SIZE},
        )
        response.raise_for_status()
        data = response.json()
        items = data.get("items", [])
        tracking_numbers.extend(item["tracking_number"] for item in items)

        total = int(data.get("total", len(tracking_numbers)))
        if len(tracking_numbers) >= total or not items:
            break
        page += 1

    return tracking_numbers


async def _fetch_tcc_results(tracking_numbers: list[str]) -> list[dict[str, Any]]:
    provider = get_tcc_client()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def fetch_one(tracking_number: str) -> dict[str, Any]:
        async with semaphore:
            try:
                result = await provider.fetch(tracking_number)
                return _jsonable(result)
            except Exception as exc:
                logger.exception("github_worker_fetch_error", tracking=tracking_number)
                return {
                    "tracking_number": tracking_number,
                    "fetch_success": False,
                    "fetch_error": str(exc),
                    "provider": getattr(provider, "provider_name", "github-actions"),
                    "events": [],
                    "payload_snapshot": {},
                }

    return await asyncio.gather(*(fetch_one(tracking_number) for tracking_number in tracking_numbers))


async def run_daily(cycle_label: str) -> None:
    backend_url = os.environ["BACKEND_URL"].rstrip("/")
    cron_token = os.environ["CRON_TOKEN"]

    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0)) as client:
        tracking_numbers = await _fetch_active_tracking_numbers(client, backend_url)
        logger.info("github_worker_shipments_loaded", count=len(tracking_numbers), cycle=cycle_label)

        results = await _fetch_tcc_results(tracking_numbers)
        payload = {
            "run_type": f"github_actions_{cycle_label}",
            "cycle_label": cycle_label,
            "results": results,
        }

        response = await client.post(
            f"{backend_url}/api/cron/ingest-tracking",
            headers={"Authorization": f"Bearer {cron_token}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("github_worker_ingest_done", jobs=data.get("jobs"), checked=data.get("checked"))

        # Enviar email diario con PDF generado por Vercel
        pdf_b64 = data.get("pdf_b64")
        pdf_filename = data.get("pdf_filename") or f"reporte_tcc_diario_{cycle_label}.pdf"
        if pdf_b64:
            report_date = datetime.utcnow().strftime("%d/%m/%Y")
            body = _daily_body_html(report_date, cycle_label)
            sent = _send_smtp_email(base64.b64decode(pdf_b64), pdf_filename, "Seguimiento TCC", body)
            logger.info("github_worker_daily_email", sent=sent, cycle=cycle_label)
        else:
            logger.warning("github_worker_no_pdf", cycle=cycle_label)

        # Enviar email semanal si es lunes y Vercel generó el consolidado
        weekly_pdf_b64 = data.get("weekly_pdf_b64")
        if weekly_pdf_b64:
            weekly_filename = data.get("weekly_pdf_filename") or "reporte_tcc_semanal.pdf"
            weekly_period = data.get("weekly_period", "")
            body = _weekly_body_html(weekly_period)
            sent = _send_smtp_email(base64.b64decode(weekly_pdf_b64), weekly_filename, "Seguimiento TCC", body)
            logger.info("github_worker_weekly_email", sent=sent)


async def main() -> None:
    configure_logging()
    if len(sys.argv) != 3 or sys.argv[1] != "daily":
        raise SystemExit("Uso: python scripts/github_tracking_worker.py daily <0700|1200|1600>")

    cycle_label = sys.argv[2]
    if cycle_label not in {"0700", "1200", "1600"}:
        raise SystemExit("cycle_label debe ser 0700, 1200 o 1600")

    await run_daily(cycle_label)


if __name__ == "__main__":
    asyncio.run(main())
