"""Contrato de proveedor de tracking TCC.

Todas las implementaciones deben devolver un payload uniforme para evitar
acoplar la aplicacion a API o HTML especifico.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.utils.date_utils import utcnow
from app.utils.status_normalizer import NormalizedStatus, normalize_status


class UpstreamTransientError(Exception):
    """Error transitorio de upstream que justifica retry (5xx, timeout de red, etc.)."""


class FetchErrorCode(StrEnum):
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    UPSTREAM_ERROR = "upstream_error"
    CAPTCHA_OR_BLOCKED = "captcha_or_blocked"
    EMPTY_RESPONSE = "empty_response"
    INVALID_TRACKING_NUMBER = "invalid_tracking_number"
    PARSE_ERROR = "parse_error"


def build_fetch_error(code: FetchErrorCode, detail: str | None = None) -> str:
    if not detail:
        return code.value
    return f"{code.value}: {detail}"


def fetch_error_code(fetch_error: str | None) -> str | None:
    if not fetch_error:
        return None
    return fetch_error.split(":", 1)[0].strip()


@dataclass(slots=True)
class TrackingEventData:
    status_raw: str
    status_normalized: str
    event_at: datetime | None
    observed_at: datetime
    notes: str | None = None
    payload_snapshot: dict[str, Any] | None = None


@dataclass(slots=True)
class TrackingResult:
    tracking_number: str
    current_status_raw: str | None
    current_status_normalized: str | None
    current_status_at: datetime | None
    destination: str | None = None
    package_type: str | None = None
    client_name: str | None = None
    events: list[TrackingEventData] = field(default_factory=list)
    payload_snapshot: dict[str, Any] = field(default_factory=dict)
    fetch_success: bool = False
    fetch_error: str | None = None
    provider: str = "unknown"

    @classmethod
    def empty_error(
        cls,
        *,
        tracking_number: str,
        provider: str,
        fetch_error: str,
        payload_snapshot: dict[str, Any] | None = None,
    ) -> "TrackingResult":
        return cls(
            tracking_number=tracking_number,
            current_status_raw=None,
            current_status_normalized=None,
            current_status_at=None,
            events=[],
            payload_snapshot=payload_snapshot or {},
            fetch_success=False,
            fetch_error=fetch_error,
            provider=provider,
        )

    @property
    def latest_event(self) -> TrackingEventData | None:
        if not self.events:
            return None
        with_date = [e for e in self.events if e.event_at]
        if with_date:
            return max(with_date, key=lambda e: e.event_at)
        return self.events[0]

    # Compatibility aliases for legacy callers.
    @property
    def success(self) -> bool:
        return self.fetch_success

    @property
    def error(self) -> str | None:
        return self.fetch_error

    @property
    def raw_payload(self) -> dict[str, Any]:
        return self.payload_snapshot


def build_tracking_event(
    *,
    status_raw: str,
    event_at: datetime | None,
    notes: str | None = None,
    observed_at: datetime | None = None,
    payload_snapshot: dict[str, Any] | None = None,
) -> TrackingEventData:
    normalized: NormalizedStatus = normalize_status(status_raw)
    return TrackingEventData(
        status_raw=status_raw.strip(),
        status_normalized=normalized.value,
        event_at=event_at,
        observed_at=observed_at or utcnow(),
        notes=notes.strip() if notes else None,
        payload_snapshot=payload_snapshot,
    )


class TrackingProvider(ABC):
    provider_name: str = "base"

    @abstractmethod
    async def fetch(self, tracking_number: str) -> TrackingResult:
        """Consulta estado de guia y retorna TrackingResult uniforme."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Valida disponibilidad del proveedor externo."""
        ...
