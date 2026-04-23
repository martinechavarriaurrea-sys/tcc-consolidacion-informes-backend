"""
Proveedor TCC via API directa (sin CAPTCHA, sin scraping).
Endpoint descubierto: POST https://tccrestify-dot-tcc-cloud.appspot.com/tracking/remesa
Header requerido: appId: 9f9b3215a2ae89f1964ded0ab4b83e5354ddf1dc8a656931e958a4dc3bcf37dd
"""
from __future__ import annotations

import re
from datetime import datetime

import httpx

from app.core.logging import get_logger
from app.integrations.tcc.base import (
    FetchErrorCode,
    TrackingProvider,
    TrackingResult,
    build_fetch_error,
    build_tracking_event,
)
from app.utils.status_normalizer import normalize_status

logger = get_logger(__name__)

_BASE_URL = "https://tccrestify-dot-tcc-cloud.appspot.com"
_ENDPOINT = "/tracking/remesa"
_APP_ID = "9f9b3215a2ae89f1964ded0ab4b83e5354ddf1dc8a656931e958a4dc3bcf37dd"

_HEADERS = {
    "Content-Type": "application/json",
    "appId": _APP_ID,
    "Origin": "https://tcc.com.co",
    "Referer": "https://tcc.com.co/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9",
}

# Mapa de palabras clave en la descripcion del estado al estado normalizado
_STATUS_KEYWORD_MAP = [
    ("entregad", "entregado"),
    ("devuelt", "devuelto"),
    ("en ruta", "en_ruta_entrega"),
    ("proceso de entrega", "en_ruta_entrega"),
    ("proceso entrega", "en_ruta_entrega"),
    ("en proceso", "en_ruta_entrega"),
    ("en tránsito", "en_transito"),
    ("en transito", "en_transito"),
    ("tránsito", "en_transito"),
    ("transito", "en_transito"),
    ("recogid", "recogido"),
    ("novedad", "novedad"),
    ("registrad", "registrado"),
    ("cerrad", "cerrado"),
    ("devoluci", "devuelto"),
]


def _parse_estado(descripcion: str) -> str:
    """Convierte la descripcion de estado TCC al estado normalizado."""
    if not descripcion:
        return "registrado"
    d = descripcion.lower()
    for keyword, estado in _STATUS_KEYWORD_MAP:
        if keyword in d:
            return estado
    return normalize_status(descripcion).value


def _strip_tz(dt: datetime | None) -> datetime | None:
    """Quita la info de timezone para compatibilidad con SQLite."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y",
    ]:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    # Extraer fecha del texto "Entregada el 20/04/2026 08:45:19 AM"
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)", raw)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y %I:%M:%S %p")
        except ValueError:
            pass
    return None


class TCCDirectApiProvider(TrackingProvider):
    provider_name = "tcc_direct_api"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=_HEADERS,
                timeout=httpx.Timeout(30),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch(self, tracking_number: str) -> TrackingResult:
        tracking = tracking_number.strip().upper()
        logger.info("tcc_direct_api_fetch", tracking=tracking)

        payload = {"remesas": {"remesa": {"numero": tracking, "esrelacion": ""}}}
        snapshot: dict = {"provider": self.provider_name, "tracking": tracking}

        try:
            client = self._get_client()
            resp = await client.post(f"{_BASE_URL}{_ENDPOINT}", json=payload)
            snapshot["http_status"] = resp.status_code

            if resp.status_code != 200:
                logger.warning("tcc_direct_api_http_error", tracking=tracking, status=resp.status_code)
                return TrackingResult.empty_error(
                    tracking_number=tracking,
                    provider=self.provider_name,
                    fetch_error=build_fetch_error(
                        FetchErrorCode.UPSTREAM_ERROR,
                        f"HTTP {resp.status_code}",
                    ),
                    payload_snapshot=snapshot,
                )

            data = resp.json()
            snapshot["response_codigo"] = data.get("respuesta", {}).get("codigo")

            remesas_wrapper = data.get("remesas", {})
            remesa_list = remesas_wrapper.get("remesa", [])
            if not isinstance(remesa_list, list):
                remesa_list = [remesa_list]

            if not remesa_list:
                return TrackingResult.empty_error(
                    tracking_number=tracking,
                    provider=self.provider_name,
                    fetch_error=build_fetch_error(
                        FetchErrorCode.INVALID_TRACKING_NUMBER,
                        "No se encontraron datos para esta guia",
                    ),
                    payload_snapshot=snapshot,
                )

            remesa = remesa_list[0]
            estado_raw = remesa.get("estadoremesa", {}).get("descripcion", "")
            fecha_entrega_raw = remesa.get("fechaentrega")
            estado_norm = _parse_estado(estado_raw)

            events = []

            # Evento principal del estado actual
            fecha_estado = _strip_tz(_parse_date(fecha_entrega_raw) or _parse_date(remesa.get("fecharemesa")))
            events.append(
                build_tracking_event(
                    status_raw=estado_raw,
                    event_at=fecha_estado,
                    notes=remesa.get("observaciones"),
                    payload_snapshot={"source": "estadoremesa"},
                )
            )

            # Eventos de novedades
            novedades_wrapper = remesa.get("novedades", {})
            novedades = novedades_wrapper.get("novedad", []) if novedades_wrapper else []
            if not isinstance(novedades, list):
                novedades = [novedades]

            for nov in novedades:
                fecha_nov = _strip_tz(_parse_date(nov.get("fechanovedad") or nov.get("fechaplanteamiento")))
                causa = nov.get("novedadprincipal") or nov.get("causa", "Novedad")
                complemento = nov.get("complementonovedad", "")
                status_text = f"Novedad: {causa}"
                if complemento:
                    status_text += f" - {complemento}"

                events.append(
                    build_tracking_event(
                        status_raw=status_text,
                        event_at=fecha_nov,
                        notes=nov.get("definicion") or nov.get("comentarios"),
                        payload_snapshot={"source": "novedad", "estado": nov.get("estado")},
                    )
                )

            # Metadata adicional
            client_name = remesa.get("nombredestinatario")
            destination = remesa.get("ciudaddestino", {}).get("descripcion")

            def _sort_key(e):
                dt = e.event_at
                if dt is None:
                    return datetime.min.replace(tzinfo=timezone.utc)
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)

            from datetime import timezone
            latest = max(events, key=_sort_key, default=None)

            logger.info(
                "tcc_direct_api_success",
                tracking=tracking,
                estado=estado_norm,
                events=len(events),
            )

            return TrackingResult(
                tracking_number=tracking,
                current_status_raw=estado_raw,
                current_status_normalized=estado_norm,
                current_status_at=_strip_tz(latest.event_at) if latest else None,
                client_name=client_name,
                destination=destination,
                events=events,
                payload_snapshot=snapshot,
                fetch_success=True,
                fetch_error=None,
                provider=self.provider_name,
            )

        except httpx.TimeoutException as exc:
            logger.warning("tcc_direct_api_timeout", tracking=tracking, exc=str(exc))
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.TIMEOUT, str(exc)),
                payload_snapshot=snapshot,
            )
        except Exception as exc:
            logger.exception("tcc_direct_api_error", tracking=tracking, exc=str(exc))
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.PARSE_ERROR, str(exc)),
                payload_snapshot=snapshot,
            )

    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            r = await client.get(_BASE_URL, timeout=10)
            return r.status_code < 500
        except Exception:
            return False
