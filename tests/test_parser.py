"""Tests del parser TCC.

Cubre todas las estrategias de extracción, señales de diagnóstico,
deduplicación, ordenamiento, metadata y casos límite.
"""

from datetime import datetime

import pytest

from app.integrations.tcc.parser import (
    _extract_first_date,
    _looks_like_status,
    _parse_date,
    parse_tracking_response,
)


# ─── _parse_date ──────────────────────────────────────────────────────────────


def test_parse_date_dd_mm_yyyy_with_time():
    dt = _parse_date("22/04/2026 14:30:00")
    assert isinstance(dt, datetime)
    assert dt.day == 22 and dt.month == 4 and dt.year == 2026


def test_parse_date_dd_mm_yyyy_without_time():
    dt = _parse_date("22/04/2026")
    assert isinstance(dt, datetime)
    assert dt.day == 22 and dt.month == 4


def test_parse_date_iso_with_time():
    dt = _parse_date("2026-04-22 14:30:00")
    assert isinstance(dt, datetime)
    assert dt.hour == 14 and dt.minute == 30


def test_parse_date_iso_without_time():
    dt = _parse_date("2026-04-22")
    assert isinstance(dt, datetime)
    assert dt.year == 2026


def test_parse_date_dashes_format():
    dt = _parse_date("22-04-2026 08:00")
    assert isinstance(dt, datetime)
    assert dt.day == 22


def test_parse_date_empty_returns_none():
    assert _parse_date("") is None
    assert _parse_date(None) is None
    assert _parse_date("   ") is None


def test_parse_date_garbage_returns_none():
    # Strings sin ningún número: dateutil fuzzy no puede extraer fecha
    assert _parse_date("fecha-invalida") is None
    assert _parse_date("texto sin digitos") is None
    # Nota: dateutil fuzzy=True puede parsear strings con números sueltos
    # ("abc 123 xyz" → año 123). Ese comportamiento es aceptado por diseño.


# ─── _extract_first_date ─────────────────────────────────────────────────────


def test_extract_first_date_from_surrounding_text():
    text = "Estado: En tránsito | Fecha: 22/04/2026 10:00 | Planta Bogotá"
    dt = _extract_first_date(text)
    assert dt is not None
    assert dt.day == 22 and dt.month == 4


def test_extract_first_date_returns_none_when_absent():
    assert _extract_first_date("Texto sin ninguna fecha aquí") is None
    assert _extract_first_date("") is None


# ─── _looks_like_status ──────────────────────────────────────────────────────


def test_looks_like_status_with_known_keyword():
    assert _looks_like_status("En tránsito hacia destino") is True
    assert _looks_like_status("Recogido en origen") is True
    assert _looks_like_status("Entregado al destinatario") is True
    assert _looks_like_status("Novedad: dirección no encontrada") is True


def test_looks_like_status_too_short_or_too_long():
    assert _looks_like_status("ab") is False
    assert _looks_like_status("a" * 181) is False


def test_looks_like_status_nav_text_excluded():
    assert _looks_like_status("Bienvenido al portal TCC") is False
    assert _looks_like_status("Inicio | Contacto | Cookies") is False


# ─── parse_tracking_response — señales de diagnóstico ────────────────────────


def test_parse_handles_empty_response():
    result = parse_tracking_response("   ", "TCC000")
    assert result.empty_response is True
    assert result.events == []
    assert "empty_response" in result.parser_warnings


def test_parse_detects_invalid_tracking_signal():
    html = "<html><body><div>No se encontraron datos para la guia consultada.</div></body></html>"
    result = parse_tracking_response(html, "TCC404")
    assert result.invalid_tracking is True
    assert result.events == []
    assert "invalid_tracking_signal_detected" in result.parser_warnings


def test_parse_detects_block_or_captcha_signal():
    html = "<html><body><h1>Security check</h1><div>Please complete the reCAPTCHA challenge</div></body></html>"
    result = parse_tracking_response(html, "TCCBLOCK")
    assert result.blocked is True
    assert "blocked_signal_detected" in result.parser_warnings


def test_parse_multiple_warnings_accumulated():
    html = "<html><body><div>Captcha required. No se encontraron datos.</div></body></html>"
    result = parse_tracking_response(html, "TCCMULTI")
    assert result.blocked is True
    assert result.invalid_tracking is True
    assert len(result.parser_warnings) >= 2


# ─── parse_tracking_response — estrategia tabla ──────────────────────────────


def test_parse_from_table_html_with_metadata():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th><th>Observacion</th></tr>
        <tr><td>En transito</td><td>22/04/2026 10:00</td><td>Planta Bogota</td></tr>
        <tr><td>Recogido</td><td>21/04/2026 08:00</td><td></td></tr>
      </table>
      <table>
        <tr><td>Cliente</td><td>Cliente Prueba S.A.S.</td></tr>
        <tr><td>Destino</td><td>Medellin</td></tr>
        <tr><td>Tipo de paquete</td><td>Sobre</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC123")
    assert len(result.events) == 2
    statuses = [e.status_raw for e in result.events]
    assert "En transito" in statuses
    assert "Recogido" in statuses
    assert result.client_name == "Cliente Prueba S.A.S."
    assert result.destination == "Medellin"
    assert result.package_type == "Sobre"
    assert result.blocked is False
    assert result.invalid_tracking is False


def test_parse_table_events_sorted_most_recent_first():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>Entregado</td><td>22/04/2026 15:00</td></tr>
        <tr><td>En ruta</td><td>22/04/2026 09:00</td></tr>
        <tr><td>En transito</td><td>21/04/2026 18:00</td></tr>
        <tr><td>Recogido</td><td>21/04/2026 08:00</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_ORDER")
    assert len(result.events) == 4
    dates = [e.event_at for e in result.events if e.event_at]
    for i in range(len(dates) - 1):
        assert dates[i] >= dates[i + 1], "Eventos deben estar ordenados de más reciente a más antiguo"


def test_parse_table_events_without_date_sorted_last():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>Entregado</td><td>22/04/2026 15:00</td></tr>
        <tr><td>En transito</td><td></td></tr>
        <tr><td>Recogido</td><td>21/04/2026 08:00</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_NODATE")
    events_with_date = [e for e in result.events if e.event_at]
    events_without = [e for e in result.events if not e.event_at]
    # todos los con fecha deben ir antes que los sin fecha
    assert all(
        result.events.index(e_with) < result.events.index(e_without)
        for e_with in events_with_date
        for e_without in events_without
    )


def test_parse_multiple_tables_all_extracted():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>Entregado</td><td>22/04/2026 15:00</td></tr>
      </table>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>En transito</td><td>21/04/2026 18:00</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_MULTI_TABLE")
    statuses = [e.status_raw for e in result.events]
    assert "Entregado" in statuses
    assert "En transito" in statuses


def test_parse_table_with_notes_extracted():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th><th>Observacion</th></tr>
        <tr><td>En transito</td><td>22/04/2026</td><td>Centro logistico Bogota</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_NOTES")
    assert len(result.events) == 1
    assert result.events[0].notes == "Centro logistico Bogota"


def test_parse_table_empty_notes_not_stored():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th><th>Observacion</th></tr>
        <tr><td>Recogido</td><td>21/04/2026</td><td>-</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_EMPTY_NOTES")
    assert len(result.events) == 1
    assert result.events[0].notes is None


def test_parse_no_metadata_returns_none():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>En transito</td><td>22/04/2026</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_NO_META")
    assert result.client_name is None
    assert result.destination is None
    assert result.package_type is None


# ─── parse_tracking_response — estrategia semántica ──────────────────────────


def test_parse_events_from_semantic_tracking_class():
    html = """
    <html><body>
      <div class="tracking-timeline">
        <div class="tracking-item">En transito 22/04/2026 10:00</div>
        <div class="tracking-item">Recogido 21/04/2026 08:00</div>
      </div>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_SEMANTIC")
    statuses_lower = [e.status_raw.lower() for e in result.events]
    assert any("transito" in s for s in statuses_lower)


def test_parse_events_from_estado_class():
    html = """
    <html><body>
      <div class="estado-envio">Entregado al destinatario - 22/04/2026</div>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_ESTADO_CLASS")
    assert len(result.events) >= 1
    assert any("entregado" in e.status_raw.lower() for e in result.events)


# ─── parse_tracking_response — estrategia text_pattern ───────────────────────


def test_parse_text_pattern_strategy():
    html = """
    <html><body>
      <p>Estado: En transito hacia destino final</p>
      <p>Fecha: 22/04/2026 10:00</p>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_TEXT")
    assert len(result.events) >= 1
    # "transito" sin tilde para que el keyword match funcione en _looks_like_status
    assert any("transito" in e.status_raw.lower() for e in result.events)


def test_parse_text_pattern_with_novedad():
    html = """
    <html><body>
      <div>Novedad: Dirección incorrecta - 21/04/2026</div>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_NOVEDAD")
    assert len(result.events) >= 1


# ─── parse_tracking_response — estrategia JSON script ────────────────────────


def test_parse_extracts_events_from_ld_json_script():
    html = """
    <html><body>
      <script type="application/ld+json">
      {
        "tracking": {
          "events": [
            {"estado": "Entregado", "fecha": "2026-04-22 15:00:00", "observacion": "Recibido por porteria"},
            {"estado": "En ruta", "fecha": "2026-04-22 10:30:00"}
          ]
        }
      }
      </script>
    </body></html>
    """
    result = parse_tracking_response(html, "TCCJSON")
    assert len(result.events) == 2
    statuses = {e.status_raw for e in result.events}
    assert "Entregado" in statuses
    assert "En ruta" in statuses


def test_parse_extracts_events_from_script_regex():
    html = """
    <html><body>
      <script>
        var trackingData = {
          "estado": "En transito",
          "fecha": "22/04/2026 08:00"
        };
      </script>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_SCRIPT_REGEX")
    assert len(result.events) >= 1
    assert any("transito" in e.status_raw.lower() for e in result.events)


# ─── parse_tracking_response — fallback de estructura parcial ────────────────


def test_parse_partial_structure_fallback():
    html = """
    <html><body>
      <div class="new-card-component">
        El envio sigue en transito hacia el centro logisitico final.
      </div>
    </body></html>
    """
    result = parse_tracking_response(html, "TCCPARTIAL")
    assert len(result.events) == 1
    assert "transito" in result.events[0].status_raw.lower()
    assert result.partial_structure is True
    assert "fallback_status_used" in result.parser_warnings


def test_parse_html_without_any_status_marks_partial():
    html = "<html><body><p>Esta pagina no tiene informacion de envios.</p></body></html>"
    result = parse_tracking_response(html, "TCC_NO_STATUS")
    assert result.partial_structure is True
    assert "no_events_extracted" in result.parser_warnings


# ─── parse_tracking_response — deduplicación ─────────────────────────────────


def test_parse_deduplicates_same_event():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>En ruta</td><td>22/04/2026 11:00</td></tr>
        <tr><td>En ruta</td><td>22/04/2026 11:00</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCCDUP")
    assert len(result.events) == 1
    assert result.events[0].status_raw == "En ruta"


def test_parse_same_status_different_dates_not_deduplicated():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>En transito</td><td>22/04/2026 18:00</td></tr>
        <tr><td>En transito</td><td>21/04/2026 08:00</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_SAME_STATUS")
    assert len(result.events) == 2


# ─── parse_tracking_response — campos de diagnóstico ─────────────────────────


def test_parse_strategy_used_reflects_table():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>Recogido</td><td>21/04/2026</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_STRATEGY")
    assert result.strategy_used == "table"


def test_parse_strategy_used_is_none_when_no_events():
    html = "<html><body><p>Sin informacion disponible aqui.</p></body></html>"
    result = parse_tracking_response(html, "TCC_NO_STRATEGY")
    assert result.strategy_used is None


# ─── parse_tracking_response — robustez ──────────────────────────────────────


def test_parse_unicode_accents_in_status():
    html = """
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        <tr><td>En tránsito hacia Bogotá</td><td>22/04/2026 10:00</td></tr>
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_UNICODE")
    assert len(result.events) == 1
    assert "tránsito" in result.events[0].status_raw


def test_parse_malformed_html_does_not_raise():
    html = "<html><body><table><tr><td>En tránsito</td><tr><td>21/04/2026"
    try:
        result = parse_tracking_response(html, "TCC_MALFORMED")
        assert isinstance(result.events, list)
    except Exception as exc:
        pytest.fail(f"parse_tracking_response no debe lanzar excepciones: {exc}")


def test_parse_large_html_completes_without_error():
    event_rows = "\n".join(
        f"<tr><td>En transito</td><td>22/04/2026 {h:02d}:00</td></tr>"
        for h in range(24)
    )
    html = f"""
    <html><body>
      <table>
        <tr><th>Estado</th><th>Fecha</th></tr>
        {event_rows}
        {"<p>" + "x" * 10000 + "</p>"}
      </table>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_LARGE")
    assert isinstance(result.events, list)
    assert len(result.events) > 0


def test_parse_metadata_from_inline_text_when_no_table():
    html = """
    <html><body>
      <p>Cliente: Distribuciones Norte S.A.S.</p>
      <p>Destino: Barranquilla</p>
      <p>Tipo de envio: Paquete</p>
      <div>Estado: En tránsito</div>
    </body></html>
    """
    result = parse_tracking_response(html, "TCC_META_TEXT")
    assert result.client_name is not None
    assert "Norte" in result.client_name or "Distribuciones" in result.client_name
    assert result.destination is not None
