"""Parser robusto de respuestas TCC.

El parser no depende de un selector unico. Usa varias estrategias y devuelve
senales diagnosticas para que el proveedor pueda decidir fallback/errores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_DATE_FORMATS = [
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
]

_BLOCKED_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"captcha",
        r"recaptcha",
        r"hcaptcha",
        r"cloudflare",
        r"access denied",
        r"forbidden",
        r"bloquead",
        r"too many requests",
        r"bot detection",
        r"security check",
    ]
]

_INVALID_TRACKING_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"gu[ii]a\s+(?:no\s+)?(?:encontrad|exist)",
        r"numero\s+de\s+gu[ii]a\s+invalido",
        r"tracking\s+number\s+invalid",
        r"sin\s+resultados",
        r"no\s+se\s+encontraron\s+datos",
        r"no\s+hay\s+informaci[oó]n",
    ]
]

_DATE_PATTERN = re.compile(
    r"("  # dd/mm/yyyy o dd-mm-yyyy
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|"  # yyyy-mm-dd
    r"\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
    r")"
)

_STATUS_KEYWORDS = (
    "registr",
    "recog",
    "transit",
    "ruta",
    "despach",
    "entreg",
    "noved",
    "devol",
    "fall",
    "reten",
    "planta",
)

_METADATA_LABELS = {
    "client_name": [
        "cliente",
        "destinatario",
        "nombre cliente",
        "nombre destinatario",
        "razon social",
    ],
    "destination": ["destino", "ciudad destino", "direccion", "municipio", "ciudad"],
    "package_type": ["tipo", "tipo de paquete", "tipo de envio", "servicio"],
}


@dataclass(slots=True)
class ParsedTrackingEvent:
    status_raw: str
    event_at: datetime | None
    notes: str | None = None
    source: str = "unknown"


@dataclass(slots=True)
class TrackingParseResult:
    tracking_number: str
    events: list[ParsedTrackingEvent] = field(default_factory=list)
    client_name: str | None = None
    destination: str | None = None
    package_type: str | None = None
    blocked: bool = False
    invalid_tracking: bool = False
    empty_response: bool = False
    partial_structure: bool = False
    parser_warnings: list[str] = field(default_factory=list)
    strategy_used: str | None = None


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _is_meaningful(value: str | None) -> bool:
    if not value:
        return False
    clean = _normalize_space(value)
    if not clean:
        return False
    if clean.lower() in {"n/a", "na", "none", "null", "-", "sin informacion"}:
        return False
    return True


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    clean = _normalize_space(raw)
    if not clean:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue

    try:
        from dateutil import parser as date_parser

        return date_parser.parse(clean, dayfirst=True, fuzzy=True)
    except Exception:
        logger.debug("tcc_parser_unrecognized_date", raw=clean)
        return None


def _extract_first_date(text: str) -> datetime | None:
    match = _DATE_PATTERN.search(text or "")
    if not match:
        return None
    return _parse_date(match.group(1))


def _looks_like_status(text: str) -> bool:
    clean = _normalize_space(text)
    if len(clean) < 3 or len(clean) > 180:
        return False
    lower = clean.lower()
    if any(keyword in lower for keyword in _STATUS_KEYWORDS):
        return True
    # fallback: texto corto con letras y sin alto ruido de html
    if re.search(r"[a-zA-Z]", clean) and not re.search(r"<[^>]+>", clean):
        if not re.search(r"(?:bienvenido|inicio|portal|contacto|cookies)", lower):
            return True
    return False


def _mark_warning(result: TrackingParseResult, warning: str) -> None:
    if warning not in result.parser_warnings:
        result.parser_warnings.append(warning)


def _detect_block_signals(text: str) -> bool:
    return any(pattern.search(text) for pattern in _BLOCKED_PATTERNS)


def _detect_invalid_tracking(text: str) -> bool:
    return any(pattern.search(text) for pattern in _INVALID_TRACKING_PATTERNS)


def _dedupe_events(events: list[ParsedTrackingEvent]) -> list[ParsedTrackingEvent]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[ParsedTrackingEvent] = []

    for event in events:
        key = (
            _normalize_space(event.status_raw).lower(),
            event.event_at.isoformat() if event.event_at else "",
            _normalize_space(event.notes or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)

    with_date = [e for e in unique if e.event_at]
    without_date = [e for e in unique if not e.event_at]
    with_date.sort(key=lambda x: x.event_at, reverse=True)
    return with_date + without_date


def _extract_metadata_from_pairs(pairs: list[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}

    for label, value in pairs:
        label_clean = _normalize_space(label).lower()
        value_clean = _normalize_space(value)
        if not _is_meaningful(value_clean):
            continue

        for field, aliases in _METADATA_LABELS.items():
            if field in result:
                continue
            if any(alias in label_clean for alias in aliases):
                result[field] = value_clean

    return result


def _extract_pairs_from_table_like(soup: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) != 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        value = cells[1].get_text(" ", strip=True)
        if _is_meaningful(label) and _is_meaningful(value):
            pairs.append((label, value))

    for container in soup.find_all(["li", "p", "div", "span"]):
        text = _normalize_space(container.get_text(" ", strip=True))
        if ":" not in text:
            continue
        label, value = text.split(":", 1)
        if _is_meaningful(label) and _is_meaningful(value):
            pairs.append((label, value))

    return pairs


def _parse_events_from_tables(soup: Any) -> list[ParsedTrackingEvent]:
    events: list[ParsedTrackingEvent] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [
            _normalize_space(cell.get_text(" ", strip=True)).lower()
            for cell in rows[0].find_all(["th", "td"])
        ]

        status_col = _find_col_index(headers, ["estado", "status", "novedad", "descripcion"])
        date_col = _find_col_index(headers, ["fecha", "hora", "date"])
        notes_col = _find_col_index(headers, ["observacion", "nota", "detalle"])

        if status_col is None:
            continue

        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            if len(cells) <= status_col:
                continue

            status_raw = _normalize_space(cells[status_col].get_text(" ", strip=True))
            if not _looks_like_status(status_raw):
                continue

            date_value: datetime | None = None
            if date_col is not None and len(cells) > date_col:
                date_value = _parse_date(cells[date_col].get_text(" ", strip=True))
            if date_value is None:
                date_value = _extract_first_date(row.get_text(" ", strip=True))

            notes: str | None = None
            if notes_col is not None and len(cells) > notes_col:
                notes_raw = _normalize_space(cells[notes_col].get_text(" ", strip=True))
                notes = notes_raw if _is_meaningful(notes_raw) else None

            events.append(
                ParsedTrackingEvent(
                    status_raw=status_raw,
                    event_at=date_value,
                    notes=notes,
                    source="table",
                )
            )

    return events


def _find_col_index(headers: list[str], candidates: list[str]) -> int | None:
    for idx, header in enumerate(headers):
        if any(candidate in header for candidate in candidates):
            return idx
    return None


def _parse_events_from_semantic_elements(soup: Any) -> list[ParsedTrackingEvent]:
    events: list[ParsedTrackingEvent] = []

    for element in soup.find_all(attrs={"class": re.compile(r"track|estado|event|novedad|timeline", re.I)}):
        text = _normalize_space(element.get_text(" ", strip=True))
        if not text:
            continue

        event_at = _extract_first_date(text)
        if event_at:
            status_raw = _normalize_space(_DATE_PATTERN.sub("", text))
        else:
            status_raw = text

        if not _looks_like_status(status_raw):
            continue

        events.append(
            ParsedTrackingEvent(
                status_raw=status_raw,
                event_at=event_at,
                notes=None,
                source="semantic",
            )
        )

    return events


def _extract_events_from_json_like(payload: Any, sink: list[ParsedTrackingEvent], depth: int = 0) -> None:
    if depth > 5:
        return

    if isinstance(payload, dict):
        status_candidates = [
            payload.get("estado"),
            payload.get("status"),
            payload.get("event"),
            payload.get("title"),
            payload.get("description"),
            payload.get("novedad"),
        ]
        status_raw = next((str(s) for s in status_candidates if isinstance(s, str) and _is_meaningful(s)), None)

        date_candidates = [
            payload.get("fecha"),
            payload.get("date"),
            payload.get("event_at"),
            payload.get("eventDate"),
            payload.get("timestamp"),
            payload.get("created_at"),
        ]
        date_raw = next((str(d) for d in date_candidates if d is not None), None)

        notes_candidates = [
            payload.get("observacion"),
            payload.get("notes"),
            payload.get("detalle"),
            payload.get("comment"),
        ]
        notes = next((str(n) for n in notes_candidates if isinstance(n, str) and _is_meaningful(n)), None)

        if status_raw and _looks_like_status(status_raw):
            sink.append(
                ParsedTrackingEvent(
                    status_raw=_normalize_space(status_raw),
                    event_at=_parse_date(date_raw),
                    notes=notes,
                    source="json",
                )
            )

        for value in payload.values():
            _extract_events_from_json_like(value, sink, depth + 1)
        return

    if isinstance(payload, list):
        for item in payload:
            _extract_events_from_json_like(item, sink, depth + 1)


def _parse_events_from_scripts(soup: Any) -> list[ParsedTrackingEvent]:
    events: list[ParsedTrackingEvent] = []

    for script in soup.find_all("script"):
        raw_content = script.string or script.get_text(" ", strip=True)
        if not raw_content:
            continue

        content = raw_content.strip()
        script_type = (script.get("type") or "").lower()

        if "ld+json" in script_type:
            try:
                payload = json.loads(content)
                _extract_events_from_json_like(payload, events)
            except json.JSONDecodeError:
                continue
            continue

        if not re.search(r"\b(?:estado|status|novedad)\b", content, re.IGNORECASE):
            continue

        for match in re.finditer(
            r"(?:\"(?:estado|status|novedad)\"\s*:\s*\"([^\"]{3,180})\")",
            content,
            re.IGNORECASE,
        ):
            status_raw = _normalize_space(match.group(1))
            if not _looks_like_status(status_raw):
                continue
            around = content[max(0, match.start() - 200): match.end() + 200]
            event_at = _extract_first_date(around)
            events.append(
                ParsedTrackingEvent(
                    status_raw=status_raw,
                    event_at=event_at,
                    notes=None,
                    source="script_regex",
                )
            )

    return events


def _parse_events_from_text_patterns(text: str) -> list[ParsedTrackingEvent]:
    events: list[ParsedTrackingEvent] = []

    regex = re.compile(
        r"(?:estado|status|novedad)\s*[:\-]\s*(?P<status>[^\n\r]{3,180})",
        re.IGNORECASE,
    )

    for match in regex.finditer(text):
        status_raw = _normalize_space(match.group("status"))
        if not _looks_like_status(status_raw):
            continue

        window = text[max(0, match.start() - 150): match.end() + 150]
        event_at = _extract_first_date(window)
        events.append(
            ParsedTrackingEvent(
                status_raw=status_raw,
                event_at=event_at,
                notes=None,
                source="text_pattern",
            )
        )

    return events


def _extract_metadata(soup: Any, text: str) -> dict[str, str]:
    pairs = _extract_pairs_from_table_like(soup)
    metadata = _extract_metadata_from_pairs(pairs)

    for field, aliases in _METADATA_LABELS.items():
        if field in metadata:
            continue
        for alias in aliases:
            pattern = re.compile(rf"{re.escape(alias)}\s*[:\-]\s*([^\n\r]{{2,120}})", re.IGNORECASE)
            match = pattern.search(text)
            if not match:
                continue
            value = _normalize_space(match.group(1))
            if _is_meaningful(value):
                metadata[field] = value
                break

    return metadata


def parse_tracking_response(html: str, tracking_number: str) -> TrackingParseResult:
    result = TrackingParseResult(tracking_number=tracking_number)

    if not html or not html.strip():
        result.empty_response = True
        _mark_warning(result, "empty_response")
        return result

    lower_text = html.lower()
    result.blocked = _detect_block_signals(lower_text)
    result.invalid_tracking = _detect_invalid_tracking(lower_text)
    if result.blocked:
        _mark_warning(result, "blocked_signal_detected")
    if result.invalid_tracking:
        _mark_warning(result, "invalid_tracking_signal_detected")

    soup = None
    soup_text = _normalize_space(html)

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        soup_text = _normalize_space(soup.get_text("\n", strip=True))
    except ImportError:
        _mark_warning(result, "beautifulsoup4_missing")
    except Exception as exc:
        _mark_warning(result, f"soup_parse_error:{exc.__class__.__name__}")

    events: list[ParsedTrackingEvent] = []

    try:
        if soup is not None:
            table_events = _parse_events_from_tables(soup)
            if table_events:
                events.extend(table_events)
                result.strategy_used = result.strategy_used or "table"

            semantic_events = _parse_events_from_semantic_elements(soup)
            if semantic_events:
                events.extend(semantic_events)
                result.strategy_used = result.strategy_used or "semantic"

            script_events = _parse_events_from_scripts(soup)
            if script_events:
                events.extend(script_events)
                result.strategy_used = result.strategy_used or "script"

            metadata = _extract_metadata(soup, soup_text)
            result.client_name = metadata.get("client_name")
            result.destination = metadata.get("destination")
            result.package_type = metadata.get("package_type")

        text_events = _parse_events_from_text_patterns(soup_text)
        if text_events:
            events.extend(text_events)
            result.strategy_used = result.strategy_used or "text_pattern"

        if not events and not result.invalid_tracking and not result.blocked:
            fallback_status = _extract_fallback_status(soup_text)
            if fallback_status:
                result.partial_structure = True
                _mark_warning(result, "fallback_status_used")
                events.append(
                    ParsedTrackingEvent(
                        status_raw=fallback_status,
                        event_at=_extract_first_date(soup_text),
                        notes=None,
                        source="keyword_fallback",
                    )
                )
                result.strategy_used = result.strategy_used or "keyword_fallback"

    except Exception as exc:
        logger.exception("tcc_parser_unexpected_error", tracking=tracking_number, exc=str(exc))
        _mark_warning(result, f"parse_exception:{exc.__class__.__name__}")

    result.events = _dedupe_events(events)

    if not result.events and not result.empty_response and not result.invalid_tracking and not result.blocked:
        result.partial_structure = True
        _mark_warning(result, "no_events_extracted")

    return result


def _extract_fallback_status(text: str) -> str | None:
    if not text:
        return None

    lines = [
        _normalize_space(raw_line)
        for raw_line in re.split(r"[\n\r]+", text)
        if _normalize_space(raw_line)
    ]

    for line in lines:
        lower = line.lower()
        if not any(keyword in lower for keyword in _STATUS_KEYWORDS):
            continue
        if len(line) > 180:
            continue
        return line

    match = re.search(
        r"(entregado|en\s+transito|en\s+ruta|novedad|recogido|devuelto|registrado)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return _normalize_space(match.group(1))
