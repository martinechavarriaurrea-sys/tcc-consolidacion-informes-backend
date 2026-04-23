"""Tests de generación PDF — verifica que los archivos PDF se crean y son válidos."""

import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.services.excel_service import DailyReportRow, WeeklyReportRow
from app.services.pdf_service import PdfService

_svc = PdfService()
_NOW = datetime(2026, 4, 22, 7, 0, tzinfo=timezone.utc)


def _daily_row(tracking: str = "TCC-001", alert: bool = False, delivered: bool = False) -> DailyReportRow:
    return DailyReportRow(
        query_date=date(2026, 4, 22),
        query_time="07:00",
        tracking_number=tracking,
        advisor_name="Asesor Ejemplo",
        client_name="Cliente SA",
        current_status="en_transito",
        current_status_raw="EN TRÁNSITO",
        last_event_at=datetime(2026, 4, 20, 10, tzinfo=timezone.utc),
        hours_without_movement=49.0 if alert else 10.0,
        days_without_movement=2.04 if alert else 0.41,
        is_delivered=delivered,
        is_alert=alert,
        observations="Alerta 72h" if alert else "",
    )


def _weekly_row(tracking: str = "TCC-001") -> WeeklyReportRow:
    return WeeklyReportRow(
        week_label="2026-04-13 al 2026-04-18",
        tracking_number=tracking,
        advisor_name="Asesor Ejemplo",
        client_name="Cliente SA",
        first_status="recogido",
        last_status="entregado",
        delivered_at=datetime(2026, 4, 17, 14, tzinfo=timezone.utc),
        total_movements=4,
        still_active=False,
        alerts_detected=0,
        observations="",
    )


def test_generate_daily_pdf_creates_file():
    rows = [_daily_row("TCC-001"), _daily_row("TCC-002", alert=True)]
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "daily.pdf"
        result = _svc.generate_daily(rows, path, "0700", date(2026, 4, 22), _NOW)
        assert result.exists()
        assert result.stat().st_size > 1000  # PDF real tiene contenido


def test_generate_daily_pdf_magic_bytes():
    """Un PDF válido empieza con '%PDF'."""
    rows = [_daily_row()]
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "daily.pdf"
        _svc.generate_daily(rows, path, "0700", date(2026, 4, 22), _NOW)
        with open(path, "rb") as f:
            header = f.read(4)
        assert header == b"%PDF"


def test_generate_weekly_pdf_creates_file():
    rows = [_weekly_row("TCC-001"), _weekly_row("TCC-002")]
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "weekly.pdf"
        result = _svc.generate_weekly(rows, date(2026, 4, 13), date(2026, 4, 18), path, _NOW)
        assert result.exists()
        assert result.stat().st_size > 1000


def test_generate_weekly_pdf_magic_bytes():
    rows = [_weekly_row()]
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "weekly.pdf"
        _svc.generate_weekly(rows, date(2026, 4, 13), date(2026, 4, 18), path, _NOW)
        with open(path, "rb") as f:
            header = f.read(4)
        assert header == b"%PDF"


def test_generate_daily_pdf_empty_rows():
    """Con lista vacía debe generar PDF sin error (solo encabezado)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "empty.pdf"
        _svc.generate_daily([], path, "1200", date(2026, 4, 22), _NOW)
        assert path.exists()


def test_generates_parent_dirs():
    rows = [_daily_row()]
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "a" / "b" / "report.pdf"
        _svc.generate_daily(rows, path, "0700", date(2026, 4, 22), _NOW)
        assert path.exists()
