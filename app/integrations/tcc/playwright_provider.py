"""
Proveedor TCC via Playwright + resolucion automatica de reCAPTCHA.
Usa audio challenge + Google Speech-to-Text (gratis, sin API key).
"""
from __future__ import annotations

import re
from datetime import datetime

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.tcc.base import (
    FetchErrorCode,
    TrackingProvider,
    TrackingResult,
    build_fetch_error,
    build_tracking_event,
)
from app.integrations.tcc.parser import parse_tracking_response

logger = get_logger(__name__)
settings = get_settings()

_TRACKING_URL = "https://tcc.com.co/courier/mensajeria/rastrear-envio/"


class TCCPlaywrightProvider(TrackingProvider):
    provider_name = "tcc_playwright"

    def __init__(self) -> None:
        self._browser = None
        self._playwright = None

    async def _get_browser(self):
        if self._browser is None or not self._browser.is_connected():
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--window-size=1366,768",
                ],
            )
        return self._browser

    async def _stealth_context(self, browser):
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="es-CO",
            timezone_id="America/Bogota",
        )
        # Inyectar JS para ocultar que es Playwright/headless
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['es-CO', 'es', 'en-US'] });
            window.chrome = { runtime: {} };
        """)
        return context

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def fetch(self, tracking_number: str) -> TrackingResult:
        tracking = tracking_number.strip().upper()
        logger.info("tcc_playwright_fetch_start", tracking=tracking)

        try:
            browser = await self._get_browser()
            context = await self._stealth_context(browser)
            page = await context.new_page()

            try:
                result = await self._fetch_with_page(page, tracking)
            finally:
                await context.close()

            return result

        except Exception as exc:
            logger.exception("tcc_playwright_unexpected_error", tracking=tracking, exc=str(exc))
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.PARSE_ERROR, str(exc)),
                payload_snapshot={"provider": self.provider_name},
            )

    async def _fetch_with_page(self, page, tracking: str) -> TrackingResult:
        import imageio_ffmpeg
        from pydub import AudioSegment
        AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()
        AudioSegment.ffprobe = imageio_ffmpeg.get_ffmpeg_exe().replace("ffmpeg", "ffprobe")

        from app.integrations.tcc.captcha_solver import solve_recaptcha

        await page.goto(_TRACKING_URL, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(1500)

        # Llenar el campo de guia
        textarea = await page.query_selector("textarea")
        if not textarea:
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.PARSE_ERROR, "No se encontro el campo de guia"),
                payload_snapshot={"provider": self.provider_name},
            )
        await textarea.fill(tracking)
        await page.wait_for_timeout(300)

        # 1) Primer click en BUSCAR para activar el reCAPTCHA
        await page.evaluate("""
            () => {
                const btn = document.querySelector('[class*=submitForm]') ||
                            document.querySelector('button[type=submit]') ||
                            document.querySelector('button');
                if (btn) btn.click();
            }
        """)
        await page.wait_for_timeout(3000)

        # 2) Ahora resolver el reCAPTCHA (que ya apareció)
        solved = await solve_recaptcha(page)
        logger.info("tcc_captcha_solve_result", tracking=tracking, solved=solved)

        # 3) Si se resolvio, re-enviar el formulario
        if solved:
            await page.wait_for_timeout(500)
            await page.evaluate("""
                () => {
                    const btn = document.querySelector('[class*=submitForm]') ||
                                document.querySelector('button[type=submit]') ||
                                document.querySelector('button');
                    if (btn) btn.click();
                }
            """)

        # Esperar resultado
        await page.wait_for_timeout(6000)

        html = await page.content()
        payload_snapshot = {
            "provider": self.provider_name,
            "html_length": len(html),
            "captcha_solved": solved,
        }

        # Solo fallar si el mensaje de error de captcha está visible/activo en el DOM
        captcha_error_visible = await page.evaluate("""
            () => {
                const alerts = document.querySelectorAll('.alert, .error, [class*=error], [class*=Error]');
                for (const el of alerts) {
                    if (el.offsetParent !== null && el.textContent.includes('captcha')) return true;
                }
                return false;
            }
        """)
        if captcha_error_visible:
            logger.warning("tcc_playwright_captcha_error_visible", tracking=tracking)
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.CAPTCHA_OR_BLOCKED, "CAPTCHA no resuelto"),
                payload_snapshot=payload_snapshot,
            )

        # Parsear el HTML resultante
        parse_result = parse_tracking_response(html, tracking)
        events = []
        for parsed_event in parse_result.events:
            status_raw = (parsed_event.status_raw or "").strip()
            if not status_raw or status_raw.lower() in ("inherit", "initial", "unset", "auto"):
                continue
            events.append(
                build_tracking_event(
                    status_raw=status_raw,
                    event_at=parsed_event.event_at,
                    notes=parsed_event.notes,
                    payload_snapshot={"source": parsed_event.source},
                )
            )

        if not events:
            return TrackingResult.empty_error(
                tracking_number=tracking,
                provider=self.provider_name,
                fetch_error=build_fetch_error(FetchErrorCode.EMPTY_RESPONSE, "Sin eventos de tracking"),
                payload_snapshot=payload_snapshot,
            )

        def get_latest(evs):
            with_date = [e for e in evs if e.event_at]
            if with_date:
                return max(with_date, key=lambda x: x.event_at)
            return evs[0] if evs else None

        latest = get_latest(events)
        return TrackingResult(
            tracking_number=tracking,
            current_status_raw=latest.status_raw if latest else None,
            current_status_normalized=latest.status_normalized if latest else None,
            current_status_at=latest.event_at if latest else None,
            events=events,
            payload_snapshot=payload_snapshot,
            fetch_success=True,
            fetch_error=None,
            provider=self.provider_name,
        )

    async def health_check(self) -> bool:
        try:
            browser = await self._get_browser()
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("https://tcc.com.co", timeout=15000)
            await context.close()
            return True
        except Exception:
            return False
