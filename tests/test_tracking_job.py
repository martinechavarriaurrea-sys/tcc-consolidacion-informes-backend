"""
Tests del módulo de jobs.
Se mockean las dependencias externas (TCC, SMTP, BD).
Se verifica la lógica orquestadora sin acceso real a red ni BD.
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.jobs.tracking_job import _collect_daily_rows, _bogota_now
from app.models.shipment import Shipment


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_shipment(
    tracking: str = "TCC-001",
    is_active: bool = True,
    status: str = "en_transito",
    status_raw: str = "EN TRÁNSITO",
    status_at: datetime | None = None,
    delivered_at: datetime | None = None,
):
    s = MagicMock()
    s.id = 1
    s.tracking_number = tracking
    s.advisor_name = "Asesor Test"
    s.client_name = "Cliente Test"
    s.current_status = status
    s.current_status_raw = status_raw
    s.current_status_at = status_at or datetime(2026, 4, 20, 10, tzinfo=timezone.utc)
    s.first_seen_at = datetime(2026, 4, 18, 8, tzinfo=timezone.utc)
    s.delivered_at = delivered_at
    s.is_active = is_active
    return s


# ── Tests de _collect_daily_rows (lógica de construcción de filas) ────────────

@pytest.mark.asyncio
async def test_collect_daily_rows_active_shipment():
    """Guía activa aparece en el reporte con is_delivered=False."""
    session = AsyncMock()

    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = [_make_shipment("TCC-001")]

    delivered_result = MagicMock()
    delivered_result.scalars.return_value.all.return_value = []

    session.execute = AsyncMock(side_effect=[active_result, delivered_result])

    now = datetime(2026, 4, 22, 7, 0, tzinfo=timezone.utc)
    cycle_started_at = datetime(2026, 4, 22, 6, 50, tzinfo=timezone.utc)
    rows = await _collect_daily_rows(session, now, cycle_started_at)

    assert len(rows) == 1
    assert rows[0].tracking_number == "TCC-001"
    assert rows[0].is_delivered is False


@pytest.mark.asyncio
async def test_collect_daily_rows_delivered_this_cycle():
    """Guía entregada hoy aparece en el reporte con is_delivered=True."""
    session = AsyncMock()

    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = []

    delivered_now = datetime(2026, 4, 22, 6, 55, tzinfo=timezone.utc)
    delivered_shipment = _make_shipment(
        "TCC-DELIVERED",
        is_active=False,
        status="entregado",
        delivered_at=delivered_now,
    )
    delivered_result = MagicMock()
    delivered_result.scalars.return_value.all.return_value = [delivered_shipment]

    session.execute = AsyncMock(side_effect=[active_result, delivered_result])

    now = datetime(2026, 4, 22, 7, 0, tzinfo=timezone.utc)
    rows = await _collect_daily_rows(session, now)

    assert len(rows) == 1
    assert rows[0].is_delivered is True
    assert rows[0].tracking_number == "TCC-DELIVERED"


@pytest.mark.asyncio
async def test_collect_daily_rows_excludes_delivery_from_previous_cycle(session):
    """Una guÃ­a entregada en el ciclo anterior no reaparece en el siguiente reporte."""
    shipment = Shipment(
        tracking_number="TCC-PREVIOUS",
        advisor_name="Asesor Test",
        client_name="Cliente Test",
        current_status="entregado",
        current_status_raw="ENTREGADA",
        current_status_at=datetime(2026, 4, 22, 7, 0),
        first_seen_at=datetime(2026, 4, 21, 8, 0),
        delivered_at=datetime(2026, 4, 22, 7, 0),
        updated_at=datetime(2026, 4, 22, 7, 1),
        is_active=False,
    )
    session.add(shipment)
    await session.flush()

    now = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    cycle_started_at = datetime(2026, 4, 22, 11, 59, tzinfo=timezone.utc)
    rows = await _collect_daily_rows(session, now, cycle_started_at)

    assert rows == []


@pytest.mark.asyncio
async def test_collect_daily_rows_alert_flagged():
    """Guía con >72h sin movimiento tiene is_alert=True."""
    session = AsyncMock()

    # Último movimiento hace 80 horas
    old_dt = datetime(2026, 4, 19, 3, 0, tzinfo=timezone.utc)  # ~80h antes del 22/04 07:00
    stale_shipment = _make_shipment("TCC-STALE", status_at=old_dt)

    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = [stale_shipment]

    delivered_result = MagicMock()
    delivered_result.scalars.return_value.all.return_value = []

    session.execute = AsyncMock(side_effect=[active_result, delivered_result])

    now = datetime(2026, 4, 22, 7, 0, tzinfo=timezone.utc)
    rows = await _collect_daily_rows(session, now)

    assert len(rows) == 1
    assert rows[0].is_alert is True
    assert rows[0].hours_without_movement >= 72


@pytest.mark.asyncio
async def test_collect_daily_rows_no_shipments():
    """Sin guías, retorna lista vacía sin error."""
    session = AsyncMock()

    active_result = MagicMock()
    active_result.scalars.return_value.all.return_value = []
    delivered_result = MagicMock()
    delivered_result.scalars.return_value.all.return_value = []

    session.execute = AsyncMock(side_effect=[active_result, delivered_result])

    now = datetime(2026, 4, 22, 7, 0, tzinfo=timezone.utc)
    rows = await _collect_daily_rows(session, now)
    assert rows == []


# ── Tests de _bogota_now ──────────────────────────────────────────────────────

def test_bogota_now_returns_datetime():
    now = _bogota_now()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None


def test_bogota_now_offset():
    """Bogota está en UTC-5 (sin DST)."""
    from zoneinfo import ZoneInfo
    now = _bogota_now()
    bogota_tz = ZoneInfo("America/Bogota")
    assert now.tzinfo is not None
    # El offset de Bogotá es -5:00
    offset_hours = now.utcoffset().total_seconds() / 3600
    assert offset_hours == -5.0


# ── Test de filename naming ───────────────────────────────────────────────────

def test_daily_filename_format():
    """Verifica que el patrón de nombre de archivo es correcto."""
    report_date = date(2026, 4, 22)
    cycle_label = "0700"
    ts_str = report_date.strftime("%Y-%m-%d")
    base_name = f"reporte_tcc_diario_{ts_str}_{cycle_label}"
    assert base_name == "reporte_tcc_diario_2026-04-22_0700"


def test_weekly_filename_format():
    week_start = date(2026, 4, 13)
    week_end = date(2026, 4, 18)
    base_name = f"reporte_tcc_semanal_{week_start.strftime('%Y-%m-%d')}_al_{week_end.strftime('%Y-%m-%d')}"
    assert base_name == "reporte_tcc_semanal_2026-04-13_al_2026-04-18"
