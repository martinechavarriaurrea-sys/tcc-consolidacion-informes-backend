"""Resolver de proveedor TCC con modo configurable y failover opcional."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.tcc.base import TrackingProvider, TrackingResult, fetch_error_code

logger = get_logger(__name__)
_settings = get_settings()

_provider_instance: TrackingProvider | None = None

_NO_FALLBACK_CODES = {
    "invalid_tracking_number",
}


class FailoverTrackingProvider(TrackingProvider):
    """Intenta proveedor primario y aplica fallback segun codigo de error."""

    provider_name = "tcc_failover"

    def __init__(self, primary: TrackingProvider, fallback: TrackingProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    async def fetch(self, tracking_number: str) -> TrackingResult:
        primary_result = await self._primary.fetch(tracking_number)
        if primary_result.fetch_success:
            return primary_result

        code = fetch_error_code(primary_result.fetch_error)
        if code in _NO_FALLBACK_CODES:
            return primary_result

        logger.warning(
            "tcc_provider_fallback_start",
            tracking=tracking_number,
            primary=self._primary.provider_name,
            fallback=self._fallback.provider_name,
            primary_error=primary_result.fetch_error,
        )

        fallback_result = await self._fallback.fetch(tracking_number)
        if fallback_result.fetch_success:
            fallback_result.payload_snapshot = {
                **fallback_result.payload_snapshot,
                "fallback_from": self._primary.provider_name,
                "fallback_reason": primary_result.fetch_error,
            }
            return fallback_result

        fallback_result.payload_snapshot = {
            **fallback_result.payload_snapshot,
            "fallback_from": self._primary.provider_name,
            "fallback_reason": primary_result.fetch_error,
            "primary_error": primary_result.fetch_error,
        }
        return fallback_result

    async def health_check(self) -> bool:
        primary_ok = await self._primary.health_check()
        if primary_ok:
            return True
        return await self._fallback.health_check()

    async def close(self) -> None:
        for provider in (self._primary, self._fallback):
            close_fn = getattr(provider, "close", None)
            if callable(close_fn):
                await close_fn()


def _normalize_mode(raw_mode: str) -> str:
    mode = (raw_mode or "").strip().lower()
    if mode == "scraping":
        return "web"
    if mode in {"web", "api", "auto"}:
        return mode

    logger.warning("tcc_client_unknown_mode", mode=raw_mode, fallback="web")
    return "web"


def _build_direct_api_provider() -> TrackingProvider:
    from app.integrations.tcc.direct_api_provider import TCCDirectApiProvider
    from app.integrations.tcc.scraper import TCCWebProvider

    direct = TCCDirectApiProvider()
    web = TCCWebProvider()
    return FailoverTrackingProvider(direct, web)


def _build_web_provider() -> TrackingProvider:
    from app.integrations.tcc.scraper import TCCWebProvider

    return TCCWebProvider()


def _build_api_provider() -> TrackingProvider:
    from app.integrations.tcc.api_provider import TCCApiProvider

    return TCCApiProvider()


def _api_is_configured(provider: TrackingProvider) -> bool:
    return bool(getattr(provider, "_configured", False))


def get_tcc_client() -> TrackingProvider:
    global _provider_instance

    if _provider_instance is not None:
        return _provider_instance

    mode = _normalize_mode(_settings.tcc_integration_mode)

    if mode == "web":
        provider = _build_direct_api_provider()
        logger.info("tcc_client_mode", mode="direct_api_with_web_fallback")
        _provider_instance = provider
        return provider

    if mode == "api":
        api_provider = _build_api_provider()
        if _settings.tcc_enable_web_fallback:
            logger.info("tcc_client_mode", mode="api_with_web_fallback")
            _provider_instance = FailoverTrackingProvider(api_provider, _build_web_provider())
        else:
            logger.info("tcc_client_mode", mode="api")
            _provider_instance = api_provider
        return _provider_instance

    # auto mode
    api_provider = _build_api_provider()
    if _api_is_configured(api_provider):
        if _settings.tcc_enable_web_fallback:
            logger.info("tcc_client_mode", mode="auto_api_with_web_fallback")
            _provider_instance = FailoverTrackingProvider(api_provider, _build_web_provider())
        else:
            logger.info("tcc_client_mode", mode="auto_api")
            _provider_instance = api_provider
    else:
        logger.info("tcc_client_mode", mode="auto_web")
        _provider_instance = _build_web_provider()

    return _provider_instance


async def reset_tcc_client() -> None:
    """Cierra el cliente actual y fuerza recreacion (util para tests)."""
    global _provider_instance

    if _provider_instance is not None:
        close_fn = getattr(_provider_instance, "close", None)
        if callable(close_fn):
            await close_fn()
        _provider_instance = None
