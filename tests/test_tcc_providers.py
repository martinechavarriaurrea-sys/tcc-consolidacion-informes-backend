"""Tests de TCCWebProvider, TCCApiProvider y FailoverTrackingProvider.

Usa pytest-httpx para interceptar requests HTTP reales y unittest.mock
para aislar errores de red sin depender de tiempos de retry.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.integrations.tcc.base import (
    FetchErrorCode,
    TrackingEventData,
    TrackingProvider,
    TrackingResult,
    UpstreamTransientError,
    build_fetch_error,
    build_tracking_event,
)
from app.integrations.tcc.client import FailoverTrackingProvider
from app.integrations.tcc.scraper import TCCWebProvider
from app.utils.date_utils import utcnow

# ─── HTML de fixtures ────────────────────────────────────────────────────────

_HTML_VALID = """
<html><body>
  <table>
    <tr><th>Estado</th><th>Fecha</th><th>Observacion</th></tr>
    <tr><td>Entregado al destinatario</td><td>22/04/2026 15:00</td><td>Recibido porteria</td></tr>
    <tr><td>En ruta de entrega</td><td>22/04/2026 09:00</td><td></td></tr>
    <tr><td>En transito</td><td>21/04/2026 18:00</td><td>Planta Bogota</td></tr>
  </table>
  <table>
    <tr><td>Cliente</td><td>Empresa Prueba S.A.S.</td></tr>
    <tr><td>Destino</td><td>Medellin</td></tr>
  </table>
</body></html>
"""

_HTML_CAPTCHA = """
<html>
<head><title>Security Check - TCC</title></head>
<body>
  <h1>Security check required</h1>
  <div class="challenge-container">
    <p>Please complete the reCAPTCHA challenge to verify you are not a bot.</p>
    <div id="recaptcha-box" data-sitekey="abc123">
      <script src="https://www.google.com/recaptcha/api.js"></script>
    </div>
  </div>
  <p>Si crees que esto es un error, contacta a soporte en soporte@tcc.com.co</p>
</body>
</html>
"""

_HTML_INVALID_TRACKING = """
<html>
<head><title>Rastrear Envío - TCC</title></head>
<body>
  <header><nav><a href="/">Inicio</a> | <a href="/contacto">Contacto</a></nav></header>
  <main>
    <section class="resultado-busqueda">
      <div class="alert alert-warning">
        <p>No se encontraron datos para la guia consultada.</p>
        <p>Por favor verifique el número de guía e intente nuevamente.</p>
      </div>
    </section>
  </main>
  <footer><p>TCC - Expertos en logística</p></footer>
</body>
</html>
"""

_HTML_PARTIAL = """
<html><body>
  <div class="estado-envio">El envio sigue en transito hacia Bogota.</div>
</body></html>
"""

_TCC_TRACKING_URL_PATTERN = re.compile(r"https://tcc\.com\.co.*")
_TCC_BASE_URL_PATTERN = re.compile(r"https://tcc\.com\.co$")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_mock_response(text: str = "", status_code: int = 200) -> MagicMock:
    mock = MagicMock(spec=httpx.Response)
    mock.text = text
    mock.status_code = status_code
    mock.url = httpx.URL("https://tcc.com.co/courier/mensajeria/rastrear-envio/?guia=TCC123")
    return mock


def _make_mock_json_response(payload: dict, status_code: int = 200) -> MagicMock:
    mock = MagicMock(spec=httpx.Response)
    mock.json.return_value = payload
    mock.status_code = status_code
    mock.url = httpx.URL("https://api.tcc.com.co/tracking/TCC123")
    return mock


# ─── TCCWebProvider — casos de éxito ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_provider_fetch_success(httpx_mock):
    httpx_mock.add_response(url=_TCC_TRACKING_URL_PATTERN, text=_HTML_VALID, status_code=200)

    provider = TCCWebProvider()
    result = await provider.fetch("TCC123456")
    await provider.close()

    assert result.fetch_success is True
    assert result.fetch_error is None
    assert result.tracking_number == "TCC123456"
    assert len(result.events) >= 3
    assert result.provider == "tcc_web"


@pytest.mark.asyncio
async def test_web_provider_fetch_success_has_current_status(httpx_mock):
    httpx_mock.add_response(url=_TCC_TRACKING_URL_PATTERN, text=_HTML_VALID, status_code=200)

    provider = TCCWebProvider()
    result = await provider.fetch("TCC123456")
    await provider.close()

    assert result.current_status_raw is not None
    assert result.current_status_normalized is not None


@pytest.mark.asyncio
async def test_web_provider_fetch_success_has_metadata(httpx_mock):
    httpx_mock.add_response(url=_TCC_TRACKING_URL_PATTERN, text=_HTML_VALID, status_code=200)

    provider = TCCWebProvider()
    result = await provider.fetch("TCC123456")
    await provider.close()

    assert result.client_name == "Empresa Prueba S.A.S."
    assert result.destination == "Medellin"


@pytest.mark.asyncio
async def test_web_provider_fetch_success_has_payload_snapshot(httpx_mock):
    httpx_mock.add_response(url=_TCC_TRACKING_URL_PATTERN, text=_HTML_VALID, status_code=200)

    provider = TCCWebProvider()
    result = await provider.fetch("TCC123456")
    await provider.close()

    snapshot = result.payload_snapshot
    assert isinstance(snapshot, dict)
    assert snapshot.get("provider") == "tcc_web"
    assert "parser" in snapshot
    assert isinstance(snapshot["parser"]["warnings"], list)


@pytest.mark.asyncio
async def test_web_provider_fetch_latest_event_is_most_recent(httpx_mock):
    httpx_mock.add_response(url=_TCC_TRACKING_URL_PATTERN, text=_HTML_VALID, status_code=200)

    provider = TCCWebProvider()
    result = await provider.fetch("TCC123456")
    await provider.close()

    # El evento más reciente es "Entregado" a las 15:00
    assert result.current_status_raw is not None
    assert "entregado" in result.current_status_raw.lower() or "Entregado" in result.current_status_raw


# ─── TCCWebProvider — detección de bloqueo ───────────────────────────────────


@pytest.mark.asyncio
async def test_web_provider_fetch_detects_captcha(httpx_mock):
    httpx_mock.add_response(url=_TCC_TRACKING_URL_PATTERN, text=_HTML_CAPTCHA, status_code=200)

    provider = TCCWebProvider()
    result = await provider.fetch("TCC999")
    await provider.close()

    assert result.fetch_success is False
    assert FetchErrorCode.CAPTCHA_OR_BLOCKED in (result.fetch_error or "")
    assert "captcha_or_blocked" in (result.fetch_error or "")


# ─── TCCWebProvider — guía inválida ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_provider_fetch_invalid_tracking(httpx_mock):
    httpx_mock.add_response(
        url=_TCC_TRACKING_URL_PATTERN, text=_HTML_INVALID_TRACKING, status_code=200
    )

    provider = TCCWebProvider()
    result = await provider.fetch("INVALIDO")
    await provider.close()

    assert result.fetch_success is False
    assert FetchErrorCode.INVALID_TRACKING_NUMBER in (result.fetch_error or "")


# ─── TCCWebProvider — errores de red (mockeados en _fetch_with_retry) ────────


@pytest.mark.asyncio
async def test_web_provider_fetch_handles_timeout():
    provider = TCCWebProvider()
    with patch.object(
        provider,
        "_fetch_with_retry",
        new=AsyncMock(side_effect=httpx.TimeoutException("timeout simulado")),
    ):
        result = await provider.fetch("TCC123")
    await provider.close()

    assert result.fetch_success is False
    assert FetchErrorCode.TIMEOUT in (result.fetch_error or "")


@pytest.mark.asyncio
async def test_web_provider_fetch_handles_network_error():
    provider = TCCWebProvider()
    with patch.object(
        provider,
        "_fetch_with_retry",
        new=AsyncMock(side_effect=httpx.NetworkError("conexión rechazada")),
    ):
        result = await provider.fetch("TCC123")
    await provider.close()

    assert result.fetch_success is False
    assert FetchErrorCode.NETWORK_ERROR in (result.fetch_error or "")


@pytest.mark.asyncio
async def test_web_provider_fetch_handles_upstream_error():
    provider = TCCWebProvider()
    with patch.object(
        provider,
        "_fetch_with_retry",
        new=AsyncMock(side_effect=UpstreamTransientError("HTTP 503")),
    ):
        result = await provider.fetch("TCC123")
    await provider.close()

    assert result.fetch_success is False
    assert FetchErrorCode.UPSTREAM_ERROR in (result.fetch_error or "")


@pytest.mark.asyncio
async def test_web_provider_fetch_handles_unexpected_exception():
    provider = TCCWebProvider()
    with patch.object(
        provider,
        "_fetch_with_retry",
        new=AsyncMock(side_effect=RuntimeError("error inesperado")),
    ):
        result = await provider.fetch("TCC123")
    await provider.close()

    assert result.fetch_success is False
    assert result.fetch_error is not None


# ─── TCCWebProvider — respuesta vacía ────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_provider_fetch_empty_response():
    provider = TCCWebProvider()
    mock_response = _make_mock_response(text="   ", status_code=200)
    with patch.object(provider, "_fetch_with_retry", new=AsyncMock(return_value=mock_response)):
        result = await provider.fetch("TCC123")
    await provider.close()

    assert result.fetch_success is False
    assert FetchErrorCode.EMPTY_RESPONSE in (result.fetch_error or "")


# ─── TCCWebProvider — health check ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_web_provider_health_check_ok(httpx_mock):
    httpx_mock.add_response(url=re.compile(r"https://tcc\.com\.co"), status_code=200, text="<html>ok</html>")

    provider = TCCWebProvider()
    ok = await provider.health_check()
    await provider.close()

    assert ok is True


@pytest.mark.asyncio
async def test_web_provider_health_check_fails_on_network_error():
    provider = TCCWebProvider()
    with patch.object(provider, "_get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=httpx.NetworkError("fail"))
        mock_client_factory.return_value = mock_client

        ok = await provider.health_check()
    await provider.close()

    assert ok is False


# ─── TCCWebProvider — tracking_number normalizado a mayúsculas ───────────────


@pytest.mark.asyncio
async def test_web_provider_normalizes_tracking_number(httpx_mock):
    httpx_mock.add_response(url=_TCC_TRACKING_URL_PATTERN, text=_HTML_VALID, status_code=200)

    provider = TCCWebProvider()
    result = await provider.fetch("  tcc123456  ")
    await provider.close()

    assert result.tracking_number == "TCC123456"


# ─── TCCApiProvider — sin configurar ─────────────────────────────────────────


def _make_api_settings(base_url: str = "https://api.tcc.com.co", api_key: str = "test-key") -> MagicMock:
    s = MagicMock()
    s.tcc_api_base_url = base_url
    s.tcc_api_key = api_key
    s.tcc_api_tracking_path = "/tracking/{tracking_number}"
    s.tcc_api_health_path = "/health"
    s.tcc_api_auth_scheme = "Bearer"
    s.tcc_request_timeout = 30
    s.tcc_max_retries = 1
    s.tcc_retry_delay = 0.001
    return s


@pytest.mark.asyncio
async def test_api_provider_returns_not_configured_when_no_credentials():
    from app.integrations.tcc.api_provider import TCCApiProvider

    with patch("app.integrations.tcc.api_provider.settings", _make_api_settings(base_url="", api_key="")):
        provider = TCCApiProvider()
        result = await provider.fetch("TCC123")

    assert result.fetch_success is False
    assert FetchErrorCode.PROVIDER_NOT_CONFIGURED in (result.fetch_error or "")


@pytest.mark.asyncio
async def test_api_provider_not_configured_has_diagnostic_snapshot():
    from app.integrations.tcc.api_provider import TCCApiProvider

    with patch("app.integrations.tcc.api_provider.settings", _make_api_settings(base_url="", api_key="")):
        provider = TCCApiProvider()
        result = await provider.fetch("TCC123")

    assert result.payload_snapshot.get("configured") is False


# ─── TCCApiProvider — _normalize_payload (unit puro) ─────────────────────────


def _make_configured_api_provider():
    from app.integrations.tcc.api_provider import TCCApiProvider

    with patch("app.integrations.tcc.api_provider.settings", _make_api_settings()):
        return TCCApiProvider()


def test_api_normalize_payload_canonical_keys():
    provider = _make_configured_api_provider()
    payload = {
        "current_status_raw": "En tránsito",
        "current_status_at": "2026-04-22 10:00:00",
        "destination": "Medellín",
        "package_type": "Sobre",
        "client_name": "Empresa S.A.S.",
        "events": [
            {"status": "En tránsito", "fecha": "2026-04-22 10:00:00", "observacion": "Planta Bogotá"},
            {"status": "Recogido", "fecha": "2026-04-21 08:00:00"},
        ],
    }
    result = provider._normalize_payload(payload)

    assert result["current_status_raw"] == "En tránsito"
    assert result["destination"] == "Medellín"
    assert result["package_type"] == "Sobre"
    assert result["client_name"] == "Empresa S.A.S."
    assert len(result["events_raw"]) == 2
    assert result["invalid_tracking"] is False


def test_api_normalize_payload_spanish_keys():
    provider = _make_configured_api_provider()
    payload = {
        "estado": "Entregado",
        "destino": "Cali",
        "tipo_paquete": "Paquete",
        "cliente": "Cliente S.A.",
        "eventos": [{"estado": "Entregado", "fecha": "2026-04-22"}],
    }
    result = provider._normalize_payload(payload)

    assert result["current_status_raw"] == "Entregado"
    assert result["destination"] == "Cali"
    assert result["client_name"] == "Cliente S.A."
    assert len(result["events_raw"]) == 1


def test_api_normalize_payload_nested_data_events():
    provider = _make_configured_api_provider()
    payload = {
        "data": {
            "events": [
                {"status": "En ruta", "fecha": "2026-04-22 09:00"},
            ]
        }
    }
    result = provider._normalize_payload(payload)
    assert len(result["events_raw"]) == 1


def test_api_normalize_payload_detects_invalid_by_found_false():
    provider = _make_configured_api_provider()
    payload = {"found": False, "message": "Guía no encontrada"}
    result = provider._normalize_payload(payload)
    assert result["invalid_tracking"] is True


def test_api_normalize_payload_detects_invalid_by_message():
    provider = _make_configured_api_provider()
    payload = {"message": "no encontrada la guía solicitada"}
    result = provider._normalize_payload(payload)
    assert result["invalid_tracking"] is True


def test_api_normalize_payload_detects_invalid_by_exists_false():
    provider = _make_configured_api_provider()
    payload = {"exists": False}
    result = provider._normalize_payload(payload)
    assert result["invalid_tracking"] is True


def test_api_normalize_payload_all_missing_fields_returns_nones():
    provider = _make_configured_api_provider()
    payload = {"some_unknown_field": "value"}
    result = provider._normalize_payload(payload)
    assert result["current_status_raw"] is None
    assert result["destination"] is None
    assert result["client_name"] is None
    assert result["events_raw"] == []


# ─── TCCApiProvider — fetch con HTTP mockeado ─────────────────────────────────


@pytest.mark.asyncio
async def test_api_provider_fetch_success():
    from app.integrations.tcc.api_provider import TCCApiProvider

    mock_payload = {
        "current_status_raw": "Entregado",
        "current_status_at": "2026-04-22 15:00:00",
        "destination": "Bogotá",
        "events": [
            {"status": "Entregado", "fecha": "2026-04-22 15:00:00"},
            {"status": "En ruta", "fecha": "2026-04-22 09:00:00"},
        ],
    }
    mock_response = _make_mock_json_response(mock_payload, status_code=200)

    with patch("app.integrations.tcc.api_provider.settings", _make_api_settings()):
        provider = TCCApiProvider()
        with patch.object(provider, "_fetch_with_retry", new=AsyncMock(return_value=mock_response)):
            result = await provider.fetch("TCC123")

    assert result.fetch_success is True
    assert result.current_status_raw == "Entregado"
    assert result.destination == "Bogotá"
    assert len(result.events) == 2
    assert result.provider == "tcc_api"


@pytest.mark.asyncio
async def test_api_provider_fetch_creates_synthetic_event_when_no_timeline():
    from app.integrations.tcc.api_provider import TCCApiProvider

    mock_payload = {"current_status_raw": "En tránsito", "current_status_at": "2026-04-22"}
    mock_response = _make_mock_json_response(mock_payload)

    with patch("app.integrations.tcc.api_provider.settings", _make_api_settings()):
        provider = TCCApiProvider()
        with patch.object(provider, "_fetch_with_retry", new=AsyncMock(return_value=mock_response)):
            result = await provider.fetch("TCC123")

    assert result.fetch_success is True
    assert len(result.events) == 1
    assert "sin historial" in (result.events[0].notes or "").lower()


@pytest.mark.asyncio
async def test_api_provider_fetch_invalid_tracking_from_payload():
    from app.integrations.tcc.api_provider import TCCApiProvider

    mock_payload = {"found": False, "message": "Guía no encontrada"}
    mock_response = _make_mock_json_response(mock_payload)

    with patch("app.integrations.tcc.api_provider.settings", _make_api_settings()):
        provider = TCCApiProvider()
        with patch.object(provider, "_fetch_with_retry", new=AsyncMock(return_value=mock_response)):
            result = await provider.fetch("INVALIDO")

    assert result.fetch_success is False
    assert FetchErrorCode.INVALID_TRACKING_NUMBER in (result.fetch_error or "")


@pytest.mark.asyncio
async def test_api_provider_fetch_handles_timeout():
    from app.integrations.tcc.api_provider import TCCApiProvider

    with patch("app.integrations.tcc.api_provider.settings", _make_api_settings()):
        provider = TCCApiProvider()
        with patch.object(
            provider,
            "_fetch_with_retry",
            new=AsyncMock(side_effect=httpx.TimeoutException("timeout")),
        ):
            result = await provider.fetch("TCC123")

    assert result.fetch_success is False
    assert FetchErrorCode.TIMEOUT in (result.fetch_error or "")


# ─── FailoverTrackingProvider ─────────────────────────────────────────────────


class _MockProvider(TrackingProvider):
    """Proveedor stub para tests de failover."""

    provider_name = "mock"

    def __init__(self, result: TrackingResult) -> None:
        self._result = result

    async def fetch(self, tracking_number: str) -> TrackingResult:
        return self._result

    async def health_check(self) -> bool:
        return self._result.fetch_success


def _success_result(provider: str = "mock_primary") -> TrackingResult:
    event = build_tracking_event(status_raw="En tránsito", event_at=utcnow())
    return TrackingResult(
        tracking_number="TCC123",
        current_status_raw="En tránsito",
        current_status_normalized="en_transito",
        current_status_at=utcnow(),
        events=[event],
        fetch_success=True,
        provider=provider,
    )


def _error_result(code: FetchErrorCode, provider: str = "mock_primary") -> TrackingResult:
    return TrackingResult.empty_error(
        tracking_number="TCC123",
        provider=provider,
        fetch_error=build_fetch_error(code),
    )


@pytest.mark.asyncio
async def test_failover_returns_primary_when_success():
    primary = _success_result("primary")
    fallback = _success_result("fallback")

    provider = FailoverTrackingProvider(_MockProvider(primary), _MockProvider(fallback))
    result = await provider.fetch("TCC123")

    assert result.fetch_success is True
    assert result.provider == "primary"


@pytest.mark.asyncio
async def test_failover_uses_fallback_on_primary_network_error():
    primary = _error_result(FetchErrorCode.NETWORK_ERROR, "primary")
    fallback = _success_result("fallback")

    provider = FailoverTrackingProvider(_MockProvider(primary), _MockProvider(fallback))
    result = await provider.fetch("TCC123")

    assert result.fetch_success is True
    assert result.provider == "fallback"
    assert "fallback_from" in result.payload_snapshot


@pytest.mark.asyncio
async def test_failover_uses_fallback_on_primary_timeout():
    primary = _error_result(FetchErrorCode.TIMEOUT, "primary")
    fallback = _success_result("fallback")

    provider = FailoverTrackingProvider(_MockProvider(primary), _MockProvider(fallback))
    result = await provider.fetch("TCC123")

    assert result.fetch_success is True


@pytest.mark.asyncio
async def test_failover_no_fallback_for_invalid_tracking():
    primary = _error_result(FetchErrorCode.INVALID_TRACKING_NUMBER, "primary")
    fallback = _success_result("fallback")

    provider = FailoverTrackingProvider(_MockProvider(primary), _MockProvider(fallback))
    result = await provider.fetch("INVALIDO")

    assert result.fetch_success is False
    assert FetchErrorCode.INVALID_TRACKING_NUMBER in (result.fetch_error or "")


@pytest.mark.asyncio
async def test_failover_both_fail_returns_fallback_result_with_primary_error():
    primary = _error_result(FetchErrorCode.TIMEOUT, "primary")
    fallback = _error_result(FetchErrorCode.NETWORK_ERROR, "fallback")

    provider = FailoverTrackingProvider(_MockProvider(primary), _MockProvider(fallback))
    result = await provider.fetch("TCC123")

    assert result.fetch_success is False
    # payload_snapshot del fallback debe tener el error del primario registrado
    assert "primary_error" in result.payload_snapshot or "fallback_reason" in result.payload_snapshot


@pytest.mark.asyncio
async def test_failover_health_check_true_when_primary_ok():
    primary = _success_result("primary")
    fallback = _error_result(FetchErrorCode.NETWORK_ERROR, "fallback")

    provider = FailoverTrackingProvider(_MockProvider(primary), _MockProvider(fallback))
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_failover_health_check_uses_fallback_when_primary_down():
    primary = _error_result(FetchErrorCode.NETWORK_ERROR, "primary")
    fallback = _success_result("fallback")

    provider = FailoverTrackingProvider(_MockProvider(primary), _MockProvider(fallback))
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_failover_health_check_false_when_both_down():
    primary = _error_result(FetchErrorCode.NETWORK_ERROR, "primary")
    fallback = _error_result(FetchErrorCode.TIMEOUT, "fallback")

    provider = FailoverTrackingProvider(_MockProvider(primary), _MockProvider(fallback))
    assert await provider.health_check() is False


# ─── TrackingResult — invariantes del contrato ───────────────────────────────


def test_tracking_result_empty_error_has_correct_shape():
    result = TrackingResult.empty_error(
        tracking_number="TCC123",
        provider="test_provider",
        fetch_error=build_fetch_error(FetchErrorCode.TIMEOUT, "detalle extra"),
    )
    assert result.fetch_success is False
    assert result.tracking_number == "TCC123"
    assert result.provider == "test_provider"
    assert result.events == []
    assert result.current_status_raw is None
    assert FetchErrorCode.TIMEOUT in result.fetch_error


def test_tracking_result_latest_event_returns_most_recent():
    now = utcnow()
    from datetime import timedelta

    old = build_tracking_event(status_raw="Recogido", event_at=now - timedelta(days=1))
    new = build_tracking_event(status_raw="En tránsito", event_at=now)

    result = TrackingResult(
        tracking_number="TCC123",
        current_status_raw="En tránsito",
        current_status_normalized="en_transito",
        current_status_at=now,
        events=[old, new],
        fetch_success=True,
        provider="test",
    )
    assert result.latest_event is not None
    assert result.latest_event.status_raw == "En tránsito"


def test_tracking_result_latest_event_none_when_no_events():
    result = TrackingResult.empty_error(
        tracking_number="TCC000",
        provider="test",
        fetch_error=build_fetch_error(FetchErrorCode.EMPTY_RESPONSE),
    )
    assert result.latest_event is None


def test_tracking_result_compatibility_aliases():
    result = _success_result()
    assert result.success is result.fetch_success
    assert result.error is result.fetch_error
    assert result.raw_payload is result.payload_snapshot
