"""Proveedor TCC basado en web publica (sin API oficial confirmada).

Objetivos:
- Requests HTTP con timeout estricto.
- Retry con backoff exponencial para errores transitorios.
- Parser desacoplado y tolerante a cambios parciales de estructura.
- Clasificacion clara de errores (captcha, guia invalida, vacio, red, parseo).
"""

from __future__ import annotations

import re

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
from app.integrations.tcc.parser import TrackingParseResult, parse_tracking_response

logger = get_logger(__name__)
settings = get_settings()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


class TCCWebProvider(TrackingProvider):
    provider_name = "tcc_web"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=_HEADERS,
                timeout=httpx.Timeout(settings.tcc_request_timeout),
                follow_redirects=True,
                verify=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def fetch(self, tracking_number: str) -> TrackingResult:
        tracking = tracking_number.strip().upper()
        logger.info("tcc_web_fetch_start", tracking=tracking)

        response: httpx.Response | None = None
        html = ""
        parse_result = TrackingParseResult(tracking_number=tracking)

        try:
            response = await self._fetch_with_retry(tracking)
            html = response.text or ""

            parse_result = parse_tracking_response(html, tracking)
            events = self._build_events(parse_result)

            payload_snapshot = self._build_payload_snapshot(
                response=response,
                html=html,
                parse_result=parse_result,
                event_count=len(events),
            )

            if parse_result.blocked:
                logger.warning("tcc_web_blocked_or_captcha", tracking=tracking)
                return TrackingResult.empty_error(
                    tracking_number=tracking,
                    provider=self.provider_name,
                    fetch_error=build_fetch_error(
                        FetchErrorCode.CAPTCHA_OR_BLOCKED,
                        "La web de TCC devolvio senales de bloqueo o captcha",
                    ),
                    payload_snapshot=payload_snapshot,
                )

            if parse_result.empty_response or not html.strip():
                logger.warning("tcc_web_empty_response", tracking=tracking)
                return TrackingResult.empty_error(
                    tracking_number=tracking,
                    provider=self.provider_name,
                    fetch_error=build_fetch_error(
                        FetchErrorCode.EMPTY_RESPONSE,
                        "Respuesta vacia de TCC",
                    ),
                    payload_snapshot=payload_snapshot,
                )

            if parse_result.invalid_tracking and not events:
                logger.info("tcc_web_invalid_tracking", tracking=tracking)
                return TrackingResult.empty_error(
                    tracking_number=tracking,
                    provider=self.provider_name,
                    fetch_error=build_fetch_error(
                        FetchErrorCode.INVALID_TRACKING_NUMBER,
                        "TCC reporta guia invalida o sin resultados",
                    ),
                    payload_snapshot=payload_snapshot,
                )

            if not events:
                logger.warning("tcc_web_no_events_after_parse", tracking=tracking)
                return TrackingResult.empty_error(
                    tracking_number=tracking,
                    provider=self.provider_name,
                    fetch_error=build_fetch_error(
                        FetchErrorCode.PARSE_ERROR,
                        "No se pudieron extraer eventos en forma confiable",
                    ),
                    payload_snapshot=payload_snapshot,
                )

            latest = _get_latest_event(events)
            return TrackingResult(
                tracking_number=tracking,
                current_status_raw=latest.status_raw if latest else None,
                current_status_normalized=latest.status_normalized if latest else None,
                current_status_at=latest.event_at if latest else None,
                destination=parse_result.destination,
                package_type=parse_result.package_type,
                client_name=parse_result.client_name,
                events=events,
                payload_snapshot=payload_snapshot,
                fetch_success=True,
                fetch_error=None,
                provider=self.provider_name,
            )

        except httpx.TimeoutException as exc:
            logger.warning("tcc_web_timeout", tracking=tracking, exc=str(exc))
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.TIMEOUT, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, html, parse_result, event_count=0),
            )
        except (httpx.NetworkError, httpx.TransportError) as exc:
            logger.warning("tcc_web_network_error", tracking=tracking, exc=str(exc))
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.NETWORK_ERROR, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, html, parse_result, event_count=0),
            )
        except UpstreamTransientError as exc:
            logger.warning("tcc_web_upstream_error", tracking=tracking, exc=str(exc))
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.UPSTREAM_ERROR, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, html, parse_result, event_count=0),
            )
        except Exception as exc:
            logger.exception("tcc_web_unexpected_error", tracking=tracking, exc=str(exc))
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.PARSE_ERROR, str(exc)),
                payload_snapshot=self._build_payload_snapshot(response, html, parse_result, event_count=0),
            )

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError, UpstreamTransientError)),
        stop=stop_after_attempt(settings.tcc_max_retries),
        wait=wait_exponential(multiplier=settings.tcc_retry_delay, min=1, max=30),
        reraise=True,
    )
    async def _fetch_with_retry(self, tracking_number: str) -> httpx.Response:
        client = self._get_client()
        params = {settings.tcc_tracking_query_param: tracking_number}
        response = await client.get(settings.tcc_tracking_url, params=params)

        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
            raise UpstreamTransientError(f"HTTP {response.status_code}")

        if response.status_code >= 400:
            raise UpstreamTransientError(f"HTTP {response.status_code}")

        self._validate_response_content(response.text)
        return response

    def _validate_response_content(self, html: str) -> None:
        if not html or not html.strip():
            raise UpstreamTransientError("respuesta vacia")

        if len(html) < settings.tcc_min_html_length:
            # No bloquea por completo: solo fuerza retry para amortiguar respuestas truncadas.
            raise UpstreamTransientError(f"respuesta corta ({len(html)} chars)")

    def _build_events(self, parse_result: TrackingParseResult):
        events = []
        for parsed_event in parse_result.events:
            status_raw = (parsed_event.status_raw or "").strip()
            if not status_raw:
                continue

            events.append(
                build_tracking_event(
                    status_raw=status_raw,
                    event_at=parsed_event.event_at,
                    notes=parsed_event.notes,
                    payload_snapshot={
                        "source": parsed_event.source,
                    },
                )
            )

        return events

    def _build_payload_snapshot(
        self,
        response: httpx.Response | None,
        html: str,
        parse_result: TrackingParseResult,
        event_count: int,
    ) -> dict[str, object]:
        compact_excerpt = re.sub(r"\s+", " ", html[:500]).strip() if html else ""

        return {
            "provider": self.provider_name,
            "url": str(response.url) if response else None,
            "http_status": response.status_code if response else None,
            "html_length": len(html) if html else 0,
            "html_excerpt": compact_excerpt,
            "event_count": event_count,
            "parser": {
                "strategy_used": parse_result.strategy_used,
                "warnings": parse_result.parser_warnings,
                "blocked": parse_result.blocked,
                "invalid_tracking": parse_result.invalid_tracking,
                "partial_structure": parse_result.partial_structure,
            },
        }

    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            response = await client.get(settings.tcc_base_url, timeout=min(10, settings.tcc_request_timeout))
            return response.status_code < 500
        except Exception as exc:
            logger.warning("tcc_web_health_check_failed", exc=str(exc))
            return False


# Alias de compatibilidad para codigo previo.
TCCScrapingProvider = TCCWebProvider


def _get_latest_event(events):
    with_date = [e for e in events if e.event_at]
    if with_date:
        return max(with_date, key=lambda x: x.event_at)
    return events[0] if events else None
