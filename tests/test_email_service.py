"""
Tests del servicio de correo.
El SMTP real se mockea — se verifica construcción del mensaje, adjuntos y retry.
"""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.email_service import (
    _build_message,
    body_alert_72h,
    body_daily_report,
    body_weekly_report,
    send_email,
)


# ── Construcción de mensaje ───────────────────────────────────────────────────

def test_build_message_basic_fields():
    to = [("Test User", "test@example.com")]
    msg = _build_message(to, "Asunto Test", "<p>Hola</p>")
    assert msg["Subject"] == "Asunto Test"
    assert "test@example.com" in msg["To"]


def test_build_message_multiple_recipients():
    to = [("User A", "a@example.com"), ("User B", "b@example.com")]
    msg = _build_message(to, "Multi", "<p>body</p>")
    assert "a@example.com" in msg["To"]
    assert "b@example.com" in msg["To"]


def test_build_message_with_attachment():
    to = [("User", "user@example.com")]
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        f.write(b"fake excel content")
        attachment_path = Path(f.name)

    msg = _build_message(to, "Con adjunto", "<p>body</p>", attachments=[attachment_path])
    # El mensaje mixto tiene más de 1 parte (alt + attachment)
    payload = msg.get_payload()
    assert len(payload) >= 2
    attachment_path.unlink()


def test_build_message_missing_attachment_skipped(caplog):
    """Si el adjunto no existe, se loguea warning pero no falla."""
    to = [("User", "user@example.com")]
    nonexistent = Path("/nonexistent/path/file.xlsx")
    msg = _build_message(to, "Sin adjunto real", "<p>body</p>", attachments=[nonexistent])
    # El mensaje se construye igual (sin el adjunto)
    assert msg["Subject"] == "Sin adjunto real"


# ── send_email (mock SMTP) ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_email_success():
    with patch("app.services.email_service._send_raw", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = None
        result = await send_email(
            to=[("Test", "test@example.com")],
            subject="Test",
            body_html="<p>Test</p>",
        )
    assert result is True
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_no_recipients():
    result = await send_email(to=[], subject="Test", body_html="<p>Test</p>")
    assert result is False


@pytest.mark.asyncio
async def test_send_email_retry_then_success():
    """Falla 2 veces, tiene éxito en el 3er intento."""
    call_count = 0

    async def mock_send(msg, subject):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("SMTP temporalmente no disponible")

    with patch("app.services.email_service._send_raw", side_effect=mock_send):
        with patch("app.services.email_service.settings") as mock_settings:
            mock_settings.email_max_retries = 3
            mock_settings.email_retry_delay = 0.01  # no esperar en tests
            mock_settings.smtp_host = "smtp.test"
            mock_settings.smtp_port = 587
            mock_settings.smtp_user = "u"
            mock_settings.smtp_password = "p"
            mock_settings.smtp_use_tls = False
            mock_settings.email_from_name = "Test"
            mock_settings.email_from_address = "noreply@test.com"
            result = await send_email(
                to=[("User", "user@example.com")],
                subject="Retry test",
                body_html="<p>ok</p>",
            )

    assert result is True
    assert call_count == 3


@pytest.mark.asyncio
async def test_send_email_all_retries_fail():
    async def always_fail(msg, subject):
        raise ConnectionError("SMTP siempre falla")

    with patch("app.services.email_service._send_raw", side_effect=always_fail):
        with patch("app.services.email_service.settings") as mock_settings:
            mock_settings.email_max_retries = 2
            mock_settings.email_retry_delay = 0.01
            mock_settings.smtp_host = "smtp.test"
            mock_settings.smtp_port = 587
            mock_settings.smtp_user = "u"
            mock_settings.smtp_password = "p"
            mock_settings.smtp_use_tls = False
            mock_settings.email_from_name = "Test"
            mock_settings.email_from_address = "noreply@test.com"
            result = await send_email(
                to=[("User", "user@example.com")],
                subject="All fail",
                body_html="<p>fail</p>",
            )

    assert result is False


# ── Builders de cuerpo ────────────────────────────────────────────────────────

def test_body_daily_report_contains_date():
    html = body_daily_report("22/04/2026", "0700")
    assert "22/04/2026" in html
    assert "0700" in html


def test_body_weekly_report_contains_dates():
    html = body_weekly_report("2026-04-13", "2026-04-18")
    assert "2026-04-13" in html
    assert "2026-04-18" in html


def test_body_alert_contains_tracking():
    shipments = [
        {"tracking_number": "TCC-999", "advisor_name": "Asesor", "current_status": "novedad", "hours": "80"},
    ]
    html = body_alert_72h(shipments)
    assert "TCC-999" in html
    assert "80h" in html
