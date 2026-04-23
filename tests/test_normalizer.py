import pytest

from app.utils.status_normalizer import NormalizedStatus, normalize_status, is_terminal, is_issue


@pytest.mark.parametrize("raw, expected", [
    ("Entregado al destinatario", NormalizedStatus.ENTREGADO),
    ("ENTREGADO", NormalizedStatus.ENTREGADO),
    ("Devuelto al remitente", NormalizedStatus.DEVUELTO),
    ("En tránsito hacia destino", NormalizedStatus.EN_TRANSITO),
    ("En ruta de entrega", NormalizedStatus.EN_RUTA),
    ("Recogido en origen", NormalizedStatus.RECOGIDO),
    ("Registrado en sistema", NormalizedStatus.REGISTRADO),
    ("Novedad: dirección incorrecta", NormalizedStatus.NOVEDAD),
    ("Intento fallido de entrega", NormalizedStatus.FALLIDO),
    ("Estado desconocido xyz", NormalizedStatus.DESCONOCIDO),
    ("", NormalizedStatus.DESCONOCIDO),
])
def test_normalize_status(raw: str, expected: NormalizedStatus):
    assert normalize_status(raw) == expected


def test_is_terminal():
    assert is_terminal(NormalizedStatus.ENTREGADO) is True
    assert is_terminal(NormalizedStatus.DEVUELTO) is True
    assert is_terminal(NormalizedStatus.FALLIDO) is True
    assert is_terminal(NormalizedStatus.EN_TRANSITO) is False
    assert is_terminal(NormalizedStatus.REGISTRADO) is False


def test_is_issue():
    assert is_issue(NormalizedStatus.NOVEDAD) is True
    assert is_issue(NormalizedStatus.DEVUELTO) is True
    assert is_issue(NormalizedStatus.FALLIDO) is True
    assert is_issue(NormalizedStatus.EN_TRANSITO) is False
    assert is_issue(NormalizedStatus.ENTREGADO) is False


def test_normalize_preserves_all_raw_values():
    raw_values = [
        "Envío en tránsito",
        "Estado no catalogado",
        "Retornado por dirección incorrecta",
        "En despacho desde planta Bogotá",
    ]
    for raw in raw_values:
        result = normalize_status(raw)
        assert isinstance(result, NormalizedStatus)
