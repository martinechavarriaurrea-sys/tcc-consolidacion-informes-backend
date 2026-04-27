"""
Servicio de correo electrónico con soporte para:
- Adjuntos múltiples (Excel + PDF)
- Múltiples destinatarios
- Reintentos con backoff exponencial (tenacity)
- Logs completos por envío
- Trazabilidad: retorna dict con resultado y metadata

POLÍTICA DE REINTENTOS
Se reintenta hasta EMAIL_MAX_RETRIES veces con delay inicial EMAIL_RETRY_DELAY segundos
(backoff exponencial x2). Si falla definitivamente, se loguea el error y retorna False.
No se lanza excepción hacia el llamador para no interrumpir el ciclo de reporte.
"""

import asyncio
import json
import os
import subprocess
import tempfile
from email import encoders as email_encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Destinatarios fijos por tipo de reporte.
# Fuente de verdad en código; se pueden sobrescribir desde BD (email_recipients).
DAILY_RECIPIENTS = [
    ("Angela Maria Diaz Cadavid", "adiaz@asteco.com.co"),
    ("Bryan Villada", "bvillada@asteco.com.co"),
]

WEEKLY_RECIPIENTS = [
    ("Juan Camilo Muñoz", "jmunoz@asteco.com.co"),
]

ALERT_RECIPIENTS = [
    ("Juan Camilo Muñoz", "jmunoz@asteco.com.co"),
    ("Bryan Villada", "bvillada@asteco.com.co"),
]


# ── Constructor de mensaje ────────────────────────────────────────────────────

def _build_message(
    to: list[tuple[str, str]],
    subject: str,
    body_html: str,
    attachments: list[Path] | None = None,
) -> MIMEMultipart:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = f"{settings.email_from_name} <{settings.email_from_address}>"
    msg["To"] = ", ".join(f"{name} <{email}>" for name, email in to)

    # Cuerpo HTML dentro de alternative
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(alt)

    # Adjuntos
    if attachments:
        for file_path in attachments:
            if not file_path.exists():
                logger.warning("email_attachment_not_found", path=str(file_path))
                continue
            with open(file_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            email_encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{file_path.name}"',
            )
            msg.attach(part)
            logger.debug("email_attachment_added", filename=file_path.name)

    return msg


def _smtp_is_configured() -> bool:
    return bool(settings.smtp_user and settings.smtp_password)


def _outlook_desktop_available() -> bool:
    return os.name == "nt"


def _send_via_outlook_sync(
    to: list[tuple[str, str]],
    subject: str,
    body_html: str,
    attachments: list[Path] | None = None,
) -> None:
    recipients = [email for _, email in to]
    payload = {
        "to": recipients,
        "subject": subject,
        "body_html": body_html,
        "attachments": [str(path) for path in (attachments or []) if path.exists()],
    }

    payload_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    script_file = tempfile.NamedTemporaryFile(delete=False, suffix=".ps1")
    try:
        with open(payload_file.name, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)

        script = r"""
param([string]$PayloadPath)
$payload = Get-Content -Raw -LiteralPath $PayloadPath | ConvertFrom-Json
$outlook = New-Object -ComObject Outlook.Application
$mail = $outlook.CreateItem(0)
$mail.To = ($payload.to -join '; ')
$mail.Subject = $payload.subject
$mail.HTMLBody = $payload.body_html
foreach ($attachment in $payload.attachments) {
    if (Test-Path -LiteralPath $attachment) {
        $null = $mail.Attachments.Add($attachment)
    }
}
$mail.Send()
"""
        with open(script_file.name, "w", encoding="utf-8") as fh:
            fh.write(script)

        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                script_file.name,
                payload_file.name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Outlook send failed")
    finally:
        for temp_path in (payload_file.name, script_file.name):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


async def _send_via_outlook(
    to: list[tuple[str, str]],
    subject: str,
    body_html: str,
    attachments: list[Path] | None = None,
) -> None:
    await asyncio.to_thread(_send_via_outlook_sync, to, subject, body_html, attachments)


# ── Envío con reintentos ──────────────────────────────────────────────────────

async def _send_raw(msg: MIMEMultipart, subject: str) -> None:
    """Envía el mensaje SMTP. Lanza excepción si falla (para tenacity)."""
    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        start_tls=settings.smtp_use_tls,
        timeout=30,
    )
    logger.info("email_sent_ok", subject=subject)


async def send_email(
    to: list[tuple[str, str]],
    subject: str,
    body_html: str,
    attachments: list[Path] | None = None,
) -> bool:
    """
    Envía un correo con adjuntos opcionales.

    Reintenta hasta EMAIL_MAX_RETRIES veces.
    Retorna True si el envío fue exitoso, False en caso contrario.
    No propaga excepciones.
    """
    if not to:
        logger.warning("email_no_recipients", subject=subject)
        return False

    recipient_list = [e for _, e in to]
    logger.info("email_attempt", subject=subject, recipients=recipient_list)

    if not _smtp_is_configured():
        if not _outlook_desktop_available():
            logger.error("email_not_configured", subject=subject, recipients=recipient_list)
            return False

        try:
            await _send_via_outlook(to, subject, body_html, attachments)
            logger.info(
                "email_sent",
                subject=subject,
                recipients=recipient_list,
                attempt=1,
                attachments=[p.name for p in (attachments or [])],
                provider="outlook_desktop",
            )
            return True
        except Exception as exc:
            logger.error(
                "email_send_failed_final",
                subject=subject,
                recipients=recipient_list,
                attempts=1,
                provider="outlook_desktop",
                error=str(exc),
            )
            return False

    msg = _build_message(to, subject, body_html, attachments)

    max_retries = settings.email_max_retries
    delay = settings.email_retry_delay

    for attempt in range(1, max_retries + 1):
        try:
            await _send_raw(msg, subject)
            logger.info(
                "email_sent",
                subject=subject,
                recipients=recipient_list,
                attempt=attempt,
                attachments=[p.name for p in (attachments or [])],
            )
            return True
        except Exception as exc:
            logger.warning(
                "email_send_attempt_failed",
                subject=subject,
                attempt=attempt,
                max=max_retries,
                error=str(exc),
            )
            if attempt < max_retries:
                wait = delay * (2 ** (attempt - 1))
                logger.info("email_retry_wait", seconds=wait)
                await asyncio.sleep(wait)

    logger.error(
        "email_send_failed_final",
        subject=subject,
        recipients=recipient_list,
        attempts=max_retries,
    )
    return False


# ── Cuerpos de correo ─────────────────────────────────────────────────────────
# Según el requerimiento: no resumen en el cuerpo, solo los adjuntos importan.
# El cuerpo es mínimo: identifica el correo y remite a los adjuntos.

def body_daily_report(report_date: str, cycle_label: str) -> str:
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px">
    <div style="border-left:4px solid #1B3A6B;padding-left:16px;margin-bottom:16px">
      <h2 style="color:#1B3A6B;margin:0">Reporte Diario de Guías TCC</h2>
      <p style="color:#666;margin:4px 0">{report_date} — Ciclo {cycle_label}</p>
    </div>
    <p>Se adjuntan los archivos Excel y PDF con el detalle del ciclo.</p>
    <p style="color:#888;font-size:12px;margin-top:32px">
      Este mensaje fue generado automáticamente por el sistema TCC ASTECO.<br/>
      No responder a este correo.
    </p>
    </body></html>
    """


def body_weekly_report(week_start: str, week_end: str) -> str:
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px">
    <div style="border-left:4px solid #1B3A6B;padding-left:16px;margin-bottom:16px">
      <h2 style="color:#1B3A6B;margin:0">Consolidado Semanal de Guías TCC</h2>
      <p style="color:#666;margin:4px 0">Semana: {week_start} al {week_end}</p>
    </div>
    <p>Se adjuntan los archivos Excel y PDF con el consolidado de la semana.</p>
    <p style="color:#888;font-size:12px;margin-top:32px">
      Este mensaje fue generado automáticamente por el sistema TCC ASTECO.<br/>
      No responder a este correo.
    </p>
    </body></html>
    """


def body_alert_72h(shipments_info: list[dict]) -> str:
    rows_html = "".join(
        f"""<tr>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{s['tracking_number']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{s['advisor_name']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{s['current_status']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{s.get('hours', '?')}h</td>
        </tr>"""
        for s in shipments_info
    )
    return f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:700px">
    <div style="background:#C0392B;color:white;padding:16px;border-radius:4px 4px 0 0">
      <h2 style="margin:0">⚠ Alerta — Guías sin movimiento por 72+ horas</h2>
    </div>
    <div style="border:1px solid #C0392B;border-top:none;padding:16px;border-radius:0 0 4px 4px">
      <p>Las siguientes guías llevan más de 72 horas sin registrar movimiento:</p>
      <table width="100%" cellspacing="0" style="border-collapse:collapse">
        <thead>
          <tr style="background:#F5F5F5">
            <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #1B3A6B">Guía</th>
            <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #1B3A6B">Asesor</th>
            <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #1B3A6B">Estado</th>
            <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #1B3A6B">Sin movimiento</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p style="color:#888;font-size:12px;margin-top:24px">
        Este mensaje fue generado automáticamente por el sistema TCC ASTECO.
      </p>
    </div>
    </body></html>
    """
