"""
Envio de reportes TCC via Microsoft Graph API.

Consulta al backend por el reporte diario pendiente del ciclo indicado,
lo envia via Microsoft Graph API y lo marca como enviado. Es idempotente:
si el reporte ya fue enviado o no existe, sale sin error ni excepcion.

Uso:
    python scripts/send_email_graph.py --cycle 0700

Variables de entorno requeridas:
    AZURE_CLIENT_ID       - ID de la aplicacion en Azure AD
    AZURE_CLIENT_SECRET   - Secreto de la aplicacion
    AZURE_TENANT_ID       - ID del directorio (tenant) en Azure AD
    SENDER_EMAIL          - Correo del remitente (debe tener licencia M365)
    RECIPIENT_EMAILS      - Destinatarios separados por coma
    BACKEND_URL           - URL base del backend
    CRON_TOKEN            - Token OIDC de GitHub Actions para autenticar al backend
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
from datetime import datetime

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

_GRAPH_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"ERROR: Variable de entorno requerida no configurada: {name}")
    return value


def _get_graph_token(client_id: str, client_secret: str, tenant_id: str) -> str:
    """Obtiene access_token de Graph API usando client_credentials (sin usuario)."""
    url = _GRAPH_TOKEN_URL.format(tenant_id=tenant_id)
    resp = httpx.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    if not resp.is_success:
        logger.error("graph_token_error status=%s body=%s", resp.status_code, resp.text[:300])
        raise RuntimeError(f"Fallo al obtener token de Graph API: HTTP {resp.status_code}")
    token = resp.json()["access_token"]
    logger.info("graph_token_obtained tenant=%s", tenant_id)
    return token


def _fetch_pending_report(backend_url: str, cron_token: str, cycle: str) -> dict | None:
    """
    Consulta el backend por el reporte PDF pendiente de envio del ciclo dado.
    Retorna el dict del reporte, o None si ya fue enviado o no existe aun.
    """
    url = f"{backend_url}/api/v1/reports/pending"
    resp = httpx.get(
        url,
        params={"cycle": cycle},
        headers={"Authorization": f"Bearer {cron_token}"},
        timeout=60,
    )
    if resp.status_code == 404:
        logger.info("no_pending_report cycle=%s (ya enviado o no generado aun)", cycle)
        return None
    if not resp.is_success:
        logger.error("fetch_pending_error status=%s body=%s", resp.status_code, resp.text[:300])
        raise RuntimeError(f"Error al consultar reporte pendiente: HTTP {resp.status_code}")
    data = resp.json()
    logger.info(
        "pending_report_found id=%s filename=%s cycle=%s",
        data.get("id"), data.get("filename"), cycle,
    )
    return data


def _mark_report_sent(backend_url: str, cron_token: str, report_id: int) -> None:
    """Marca el reporte como enviado en el backend para garantizar idempotencia."""
    url = f"{backend_url}/api/v1/reports/{report_id}/mark_sent"
    resp = httpx.patch(
        url,
        headers={"Authorization": f"Bearer {cron_token}"},
        timeout=30,
    )
    if not resp.is_success:
        logger.error(
            "mark_sent_error id=%s status=%s body=%s",
            report_id, resp.status_code, resp.text[:300],
        )
        raise RuntimeError(f"Error al marcar reporte como enviado: HTTP {resp.status_code}")
    logger.info("report_marked_sent id=%s", report_id)


def _build_body_html(cycle: str, report_date: str) -> str:
    cycle_display = f"{cycle[:2]}:{cycle[2:]}"
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


def _send_via_graph(
    graph_token: str,
    sender: str,
    recipients: list[str],
    subject: str,
    body_html: str,
    pdf_bytes: bytes,
    pdf_filename: str,
) -> None:
    """Envia email con adjunto PDF via POST a Graph API /sendMail."""
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": r}} for r in recipients],
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": pdf_filename,
                    "contentType": "application/pdf",
                    "contentBytes": base64.b64encode(pdf_bytes).decode(),
                }
            ],
        },
        "saveToSentItems": "true",
    }

    url = _GRAPH_SEND_URL.format(sender=sender)
    resp = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {graph_token}",
            "Content-Type": "application/json",
        },
        content=json.dumps(payload).encode("utf-8"),
        timeout=90,
    )
    if not resp.is_success:
        logger.error("graph_send_error status=%s body=%s", resp.status_code, resp.text[:500])
        raise RuntimeError(f"Fallo al enviar email via Graph API: HTTP {resp.status_code}")

    logger.info(
        "graph_email_sent sender=%s recipients=%s filename=%s",
        sender, recipients, pdf_filename,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Envia reporte TCC via Microsoft Graph API")
    parser.add_argument(
        "--cycle",
        required=True,
        choices=["0700", "1200", "1600"],
        help="Ciclo del reporte a enviar",
    )
    args = parser.parse_args()
    cycle = args.cycle

    client_id = _require_env("AZURE_CLIENT_ID")
    client_secret = _require_env("AZURE_CLIENT_SECRET")
    tenant_id = _require_env("AZURE_TENANT_ID")
    sender_email = _require_env("SENDER_EMAIL")
    recipient_emails = [r.strip() for r in _require_env("RECIPIENT_EMAILS").split(",") if r.strip()]
    backend_url = _require_env("BACKEND_URL").rstrip("/")
    cron_token = _require_env("CRON_TOKEN")

    logger.info("send_email_graph_start cycle=%s backend=%s recipients=%s", cycle, backend_url, recipient_emails)

    # 1. Verificar si hay reporte pendiente — salida limpia si ya fue enviado
    report = _fetch_pending_report(backend_url, cron_token, cycle)
    if report is None:
        logger.info("send_email_graph_exit reason=no_pending_report cycle=%s", cycle)
        sys.exit(0)

    report_id: int = report["id"]
    pdf_b64: str | None = report.get("content_b64")
    pdf_filename: str = report.get("filename") or f"reporte_tcc_diario_{cycle}.pdf"

    if not pdf_b64:
        logger.warning(
            "send_email_graph_no_content report_id=%s — reporte en BD sin PDF almacenado, skip",
            report_id,
        )
        sys.exit(0)

    pdf_bytes = base64.b64decode(pdf_b64)
    logger.info(
        "send_email_graph_pdf_ready report_id=%s filename=%s bytes=%s",
        report_id, pdf_filename, len(pdf_bytes),
    )

    # 2. Obtener token de Graph API via client_credentials
    graph_token = _get_graph_token(client_id, client_secret, tenant_id)

    # 3. Enviar email
    report_date = datetime.utcnow().strftime("%d/%m/%Y")
    body_html = _build_body_html(cycle, report_date)
    _send_via_graph(
        graph_token=graph_token,
        sender=sender_email,
        recipients=recipient_emails,
        subject="Seguimiento TCC",
        body_html=body_html,
        pdf_bytes=pdf_bytes,
        pdf_filename=pdf_filename,
    )

    # 4. Marcar como enviado — garantiza idempotencia en reruns
    _mark_report_sent(backend_url, cron_token, report_id)

    logger.info("send_email_graph_done cycle=%s report_id=%s", cycle, report_id)


if __name__ == "__main__":
    main()
