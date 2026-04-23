"""Proveedor TCC via API (opcional y configurable).

No asume un contrato fijo de TCC: intenta normalizar estructuras comunes de
payload y retorna un resultado uniforme.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.tcc.base import (
    FetchErrorCode,
    TrackingProvider,
    TrackingResult,
    UpstreamTransientError,
    build_fetch_error,
    build_tracking_event,
)
from app.integrations.tcc.parser import _parse_date

logger = get_logger(__name__)
settings = get_settings()

_INVALID_MESSAGE_RE = re.compile(
    r"(no\s+encontrad|gu[ii]a\s+invalida|sin\s+resultados|invalid\s+tracking)",
    re.IGNORECASE,
)


class TCCApiProvider(TrackingProvider):
    provider_name = "tcc_api"

    def __init__(self) -> None:
        self._configured = bool(settings.tcc_api_base_url and settings.tcc_api_key)
        self._client: httpx.AsyncClient | None = None

        if not self._configured:
            logger.warning("tcc_api_not_configured")
            return

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        api_key = settings.tcc_api_key.strip()
        if api_key:
            scheme = settings.tcc_api_auth_scheme.strip()
            headers["Authorization"] = f"{scheme} {api_key}".strip() if scheme else api_key

        self._client = httpx.AsyncClient(
            base_url=settings.tcc_api_base_url,
            headers=headers,
            timeout=httpx.Timeout(settings.tcc_request_timeout),
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch(self, tracking_number: str) -> TrackingResult:
        tracking = tracking_number.strip().upper()

        if not self._configured or not self._client:
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(
                    FetchErrorCode.PROVIDER_NOT_CONFIGURED,
                    "Configura TCC_API_BASE_URL y TCC_API_KEY",
                ),
                payload_snapshot={
                    "provider": self.provider_name,
                    "configured": False,
                },
            )

        logger.info("tcc_api_fetch_start", tracking=tracking)

        response: httpx.Response | None = None
        payload: dict[str, Any] | None = None

        try:
            response = await self._fetch_with_retry(tracking)
            payload = response.json()
            if not isinstance(payload, dict):
                payload = {"raw_payload": payload}

            normalized = self._normalize_payload(payload)
            events = self._build_events(normalized)

            if not events and normalized.get("current_status_raw"):
                # Fallback minimo cuando API da estado actual pero no timeline.
                events = [
                    build_tracking_event(
                        status_raw=str(normalized["current_status_raw"]),
                        event_at=normalized.get("current_status_at"),
                        notes="Estado recibido sin historial detallado",
                        payload_snapshot={"source": "api_current_status"},
                    )
                ]

            if normalized.get("invalid_tracking") and not events:
                return TrackingResult.empty_error(
                    tracking_number=tracking,
                    provider=self.provider_name,
                    fetch_error=build_fetch_error(
                        FetchErrorCode.INVALID_TRACKING_NUMBER,
                        "API reporta guia invalida o sin resultados",
                    ),
                    payload_snapshot=self._build_payload_snapshot(response, payload, event_count=0),
                )

            if not events:
                return TrackingResult.empty_error(
                    tracking_number=tracking,
                    provider=self.provider_name,
                    fetch_error=build_fetch_error(
                        FetchErrorCode.EMPTY_RESPONSE,
                        "API sin eventos ni estado util",
                    ),
                    payload_snapshot=self._build_payload_snapshot(response, payload, event_count=0),
                )

            latest = _get_latest_event(events)
            current_status_raw = normalized.get("current_status_raw") or (latest.status_raw if latest else None)
            current_status_normalized = normalized.get("current_status_normalized") or (
                latest.status_normalized if latest else None
            )
            current_status_at = normalized.get("current_status_at") or (latest.event_at if latest else None)

            return TrackingResult(
                tracking_number=tracking,
                current_status_raw=current_status_raw,
                current_status_normalized=current_status_normalized,
                current_status_at=current_status_at,
                destination=normalized.get("destination"),
                package_type=normalized.get("package_type"),
                client_name=normalized.get("client_name"),
                events=events,
                payload_snapshot=self._build_payload_snapshot(response, payload, event_count=len(events)),
                fetch_success=True,
                fetch_error=None,
                provider=self.provider_name,
            )

        except httpx.TimeoutException as exc:
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.TIMEOUT, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, payload, event_count=0),
            )
        except (httpx.NetworkError, httpx.TransportError) as exc:
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.NETWORK_ERROR, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, payload, event_count=0),
            )
        except UpstreamTransientError as exc:
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.UPSTREAM_ERROR, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, payload, event_count=0),
            )
        except ValueError as exc:
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.PARSE_ERROR, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, payload, event_count=0),
            )
        except Exception as exc:
            logger.exception("tcc_api_fetch_unexpected_error", tracking=tracking, exc=str(exc))
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.PARSE_ERROR, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, payload, event_count=0),
            )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, UpstreamTransientError)),
        stop=stop_after_attempt(settings.tcc_max_retries),
        wait=wait_exponential(multiplier=settings.tcc_retry_delay, min=1, max=20),
        reraise=True,
    )
    async def _fetch_with_retry(self, tracking_number: str) -> httpx.Response:
        assert self._client is not None

        path = settings.tcc_api_tracking_path.format(tracking_number=tracking_number)
        response = await self._client.get(path)

        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
            raise UpstreamTransientError(f"HTTP {response.status_code}")

        if response.status_code == 404:
            raise ValueError("Endpoint de tracking API no encontrado (404)")

        if response.status_code >= 400:
            raise UpstreamTransientError(f"HTTP {response.status_code}")

        return response

    async def health_check(self) -> bool:
        if not self._configured or not self._client:
            return False

        try:
            response = await self._client.get(settings.tcc_api_health_path)
            return response.status_code < 500
        except Exception:
            return False

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        current_status_raw = _pick_first_string(
            payload,
            [
                "current_status_raw",
                "currentStatus",
                "current_status",
                "estado_actual",
                "estado",
                "status",
            ],
        )
        current_status_at = _pick_first_date(
            payload,
            [
                "current_status_at",
                "currentStatusAt",
                "fecha_estado_actual",
                "updated_at",
                "timestamp",
            ],
        )

        destination = _pick_first_string(
            payload,
            [
                "destination",
                "destino",
                "ciudad_destino",
                "direccion_entrega",
            ],
        )
        package_type = _pick_first_string(
            payload,
            ["package_type", "tipo_paquete", "service", "servicio", "tipo_servicio"],
        )
        client_name = _pick_first_string(
            payload,
            ["client_name", "cliente", "customer", "destinatario"],
        )

        invalid_tracking = self._detect_invalid_payload(payload)

        current_status_normalized = None
        if current_status_raw:
            current_status_normalized = build_tracking_event(
                status_raw=current_status_raw,
                event_at=current_status_at,
            ).status_normalized

        return {
            "current_status_raw": current_status_raw,
            "current_status_normalized": current_status_normalized,
            "current_status_at": current_status_at,
            "destination": destination,
            "package_type": package_type,
            "client_name": client_name,
            "events_raw": _extract_event_objects(payload),
            "invalid_tracking": invalid_tracking,
        }

    def _build_events(self, normalized: dict[str, Any]):
        events = []

        for obj in normalized.get("events_raw", []):
            if not isinstance(obj, dict):
                continue

            status_raw = _pick_first_string(
                obj,
                ["status_raw", "status", "estado", "novedad", "description", "detalle"],
            )
            if not status_raw:
                continue

            event_at = _pick_first_date(obj, ["event_at", "fecha", "date", "timestamp", "created_at"])
            notes = _pick_first_string(obj, ["notes", "observacion", "detalle", "comment"])

            events.append(
                build_tracking_event(
                    status_raw=status_raw,
                    event_at=event_at,
                    notes=notes,
                    payload_snapshot={"source": "api_event", "raw": obj},
                )
            )

        # Orden consistente: ultimo evento primero.
        with_date = [e for e in events if e.event_at]
        without_date = [e for e in events if not e.event_at]
        with_date.sort(key=lambda x: x.event_at, reverse=True)
        return with_date + without_date

    def _detect_invalid_payload(self, payload: dict[str, Any]) -> bool:
        bool_not_found = payload.get("found") is False or payload.get("exists") is False
        if bool_not_found:
            return True

        message_candidates = [
            payload.get("message"),
            payload.get("error"),
            payload.get("detail"),
        ]
        for message in message_candidates:
            if isinstance(message, str) and _INVALID_MESSAGE_RE.search(message):
                return True

        return False

    def _build_payload_snapshot(
        self,
        response: httpx.Response | None,
        payload: dict[str, Any] | None,
        event_count: int,
    ) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "configured": self._configured,
            "url": str(response.url) if response else None,
            "http_status": response.status_code if response else None,
            "event_count": event_count,
            "payload_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
        }


def _extract_event_objects(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        payload.get("eventos"),
        payload.get("events"),
        _safe_get(payload, "tracking", "events"),
        _safe_get(payload, "data", "events"),
        _safe_get(payload, "result", "events"),
        _safe_get(payload, "data", "eventos"),
    ]

    for candidate in candidates:
        if isinstance(candidate, list):
            return [obj for obj in candidate if isinstance(obj, dict)]

    return []


def _safe_get(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _pick_first_string(payload: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _pick_first_date(payload: dict[str, Any], keys: list[str]) -> datetime | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value
        parsed = _parse_date(str(value))
        if parsed:
            return parsed
    return None


def _get_latest_event(events):
    with_date = [e for e in events if e.event_at]
    if with_date:
        return max(with_date, key=lambda x: x.event_at)
    return events[0] if events else None
