"""
Verifica alertas de 72h en Vercel y envia email si hay nuevas.
Se ejecuta desde GitHub Actions cada 30 minutos.
"""
from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def _alert_body_html(alerts: list[dict]) -> str:
    rows = "".join(
        f"""<tr>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{a["tracking_number"]}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{a["advisor_name"]}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{a["current_status"]}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #eee">{a.get("hours", "?")}h</td>
        </tr>"""
        for a in alerts
    )
    return f"""<html><body style="font-family:Arial,sans-serif;color:#333;max-width:700px">
<div style="background:#C0392B;color:white;padding:16px;border-radius:4px 4px 0 0">
  <h2 style="margin:0">Alerta - Guias sin movimiento por 72+ horas</h2>
</div>
<div style="border:1px solid #C0392B;border-top:none;padding:16px;border-radius:0 0 4px 4px">
  <p>Las siguientes guias llevan mas de 72 horas sin registrar movimiento:</p>
  <table width="100%" cellspacing="0" style="border-collapse:collapse">
    <thead>
      <tr style="background:#F5F5F5">
        <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #1B3A6B">Guia</th>
        <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #1B3A6B">Asesor</th>
        <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #1B3A6B">Estado</th>
        <th style="padding:8px 10px;text-align:left;border-bottom:2px solid #1B3A6B">Sin movimiento</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="color:#888;font-size:12px;margin-top:24px">
    Generado automaticamente por el sistema TCC ASTECO.
  </p>
</div>
</body></html>"""


async def main() -> None:
    configure_logging()
    backend_url = os.environ["BACKEND_URL"].rstrip("/")
    cron_token = os.environ["CRON_TOKEN"]
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_host = os.getenv("SMTP_HOST", "smtp.office365.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    recipient = os.getenv("EMAIL_RECIPIENT", "echavarriam@asteco.com.co")

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.get(
            f"{backend_url}/api/cron/alert-data",
            headers={"Authorization": f"Bearer {cron_token}"},
        )
        resp.raise_for_status()
        data = resp.json()

    alerts = data.get("new_alerts", [])
    logger.info("alert_worker_checked", count=len(alerts))

    if not alerts:
        return

    if not smtp_user or not smtp_password:
        logger.warning("alert_worker_smtp_not_configured")
        return

    html = _alert_body_html(alerts)
    msg = MIMEMultipart()
    msg["Subject"] = "Seguimiento TCC"
    msg["From"] = smtp_user
    msg["To"] = recipient
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(smtp_user, [recipient], msg.as_bytes())

    logger.info("alert_worker_email_sent", alerts=len(alerts), recipient=recipient, host=smtp_host)


if __name__ == "__main__":
    asyncio.run(main())
