"""
Tests de lógica de alertas:
- is_older_than_hours
- normalize_status
- lógica de cierre (entregado → is_active=False)
- lógica de alerta 72h (sin movimiento)
- parser de estados TCC
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.utils.date_utils import hours_since, is_older_than_hours
from app.utils.status_normalizer import (
    NormalizedStatus,
    is_issue,
    is_terminal,
    normalize_status,
)


# ── date_utils ────────────────────────────────────────────────────────────────

def test_is_older_than_hours_true():
    old = datetime.now(tz=timezone.utc) - timedelta(hours=73)
    assert is_older_than_hours(old, 72) is True


def test_is_older_than_hours_false():
    recent = datetime.now(tz=timezone.utc) - timedelta(hours=10)
    assert is_older_than_hours(recent, 72) is False


def test_is_older_than_hours_boundary():
    exactly = datetime.now(tz=timezone.utc) - timedelta(hours=72, seconds=1)
    assert is_older_than_hours(exactly, 72) is True


def test_hours_since_positive():
    past = datetime.now(tz=timezone.utc) - timedelta(hours=5)
    assert 4.9 < hours_since(past) < 5.1


def test_hours_since_naive_dt():
    """Datetime sin timezone debe ser tratado como UTC."""
    past = datetime.utcnow() - timedelta(hours=3)
    result = hours_since(past)
    assert 2.9 < result < 3.1


# ── status_normalizer ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("ENTREGADO AL DESTINATARIO", NormalizedStatus.ENTREGADO),
    ("En tránsito - Planta Bogotá", NormalizedStatus.EN_TRANSITO),
    ("NOVEDAD EN ENTREGA", NormalizedStatus.NOVEDAD),
    ("Recogido por mensajero", NormalizedStatus.RECOGIDO),
    ("Devuelto al remitente", NormalizedStatus.DEVUELTO),
    ("En ruta de entrega", NormalizedStatus.EN_RUTA),
    ("Registrado en sistema", NormalizedStatus.REGISTRADO),
    ("", NormalizedStatus.DESCONOCIDO),
    ("Estado completamente desconocido XYZ", NormalizedStatus.DESCONOCIDO),
])
def test_normalize_status(raw, expected):
    assert normalize_status(raw) == expected


def test_normalize_status_case_insensitive():
    assert normalize_status("ENTREGADO") == NormalizedStatus.ENTREGADO
    assert normalize_status("entregado") == NormalizedStatus.ENTREGADO


def test_is_terminal():
    assert is_terminal(NormalizedStatus.ENTREGADO) is True
    assert is_terminal(NormalizedStatus.DEVUELTO) is True
    assert is_terminal(NormalizedStatus.FALLIDO) is True
    assert is_terminal(NormalizedStatus.EN_TRANSITO) is False
    assert is_terminal(NormalizedStatus.NOVEDAD) is False


def test_is_issue():
    assert is_issue(NormalizedStatus.NOVEDAD) is True
    assert is_issue(NormalizedStatus.DEVUELTO) is True
    assert is_issue(NormalizedStatus.FALLIDO) is True
    assert is_issue(NormalizedStatus.ENTREGADO) is False
    assert is_issue(NormalizedStatus.EN_TRANSITO) is False


# ── Lógica de cierre de guías (simulada) ─────────────────────────────────────

def test_delivered_shipment_becomes_inactive():
    """
    Simula que cuando se detecta estado ENTREGADO, is_active=False.
    Esto es responsabilidad de TrackingService._process_one; verificamos la lógica.
    """
    status = normalize_status("ENTREGADO AL DESTINATARIO")
    assert status == NormalizedStatus.ENTREGADO
    assert is_terminal(status)
    # En el tracking service: si new_status == ENTREGADO → is_active = False
    is_active = status != NormalizedStatus.ENTREGADO
    assert is_active is False


def test_novedad_shipment_stays_active():
    status = normalize_status("NOVEDAD EN ENTREGA")
    assert status == NormalizedStatus.NOVEDAD
    assert not is_terminal(status)
    is_active = status != NormalizedStatus.ENTREGADO
    assert is_active is True


# ── Lógica 72h sin movimiento ─────────────────────────────────────────────────

def test_alert_threshold_72h():
    last_movement_71h_ago = datetime.now(tz=timezone.utc) - timedelta(hours=71)
    last_movement_73h_ago = datetime.now(tz=timezone.utc) - timedelta(hours=73)

    assert is_older_than_hours(last_movement_71h_ago, 72) is False
    assert is_older_than_hours(last_movement_73h_ago, 72) is True


def test_alert_not_triggered_for_delivered():
    """Guía entregada no debe disparar alerta aunque tenga >72h sin movimiento."""
    status = NormalizedStatus.ENTREGADO
    is_active = status != NormalizedStatus.ENTREGADO
    # Si is_active=False no se evalúa para alertas
    should_alert = is_active and is_older_than_hours(
        datetime.now(tz=timezone.utc) - timedelta(hours=100), 72
    )
    assert should_alert is False
