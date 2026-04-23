"""
Resuelve reCAPTCHA v2 via desafio de audio + Google Speech-to-Text (gratis).
Flujo:
  1. Cierra el modal de advertencia de TCC
  2. Hace click en el checkbox del reCAPTCHA via JS
  3. Espera que aparezca el image challenge
  4. Cambia al audio challenge
  5. Descarga el audio, lo transcribe con Google STT (gratis)
  6. Ingresa la respuesta y verifica
"""
from __future__ import annotations
import os
import tempfile
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


async def solve_recaptcha(page) -> bool:
    try:
        # 1) Cerrar el modal de advertencia que aparece al hacer click en BUSCAR
        await _close_modal(page)
        await page.wait_for_timeout(1000)

        # 2) Hacer click en el checkbox del anchor frame via JS
        anchor_frame = _find_frame(page, "anchor")
        if not anchor_frame:
            logger.warning("recaptcha_anchor_frame_not_found")
            return False

        await anchor_frame.evaluate(
            "() => { const c=document.querySelector('#recaptcha-anchor'); if(c) c.click(); }"
        )
        await page.wait_for_timeout(5000)  # Esperar que cargue el challenge

        # 3) Comprobar si se resolvio automaticamente
        auto_solved = await anchor_frame.evaluate(
            "() => { const c=document.querySelector('#recaptcha-anchor'); return c ? c.getAttribute('aria-checked') : 'not_found'; }"
        )
        if auto_solved == "true":
            logger.info("recaptcha_auto_solved")
            return True

        # 4) Buscar el bframe con el challenge
        bframe = _find_frame(page, "bframe")
        if not bframe:
            logger.warning("recaptcha_bframe_not_found")
            return False

        # 5) Cambiar a audio challenge via JS
        audio_clicked = await bframe.evaluate(
            "() => { const b=document.querySelector('#recaptcha-audio-button'); if(b){b.click();return true;} return false; }"
        )
        if not audio_clicked:
            logger.warning("recaptcha_audio_btn_not_found")
            return False

        await page.wait_for_timeout(3000)

        # 6) Obtener la URL del audio
        audio_url = await bframe.evaluate("""
            () => {
                const a = document.querySelector('.rc-audiochallenge-tdownload-link') ||
                          document.querySelector('a[href*=".mp3"]') ||
                          document.querySelector('[download]');
                return a ? a.href : null;
            }
        """)

        if not audio_url:
            logger.warning("recaptcha_audio_url_not_found")
            return False

        logger.info("recaptcha_audio_url_found", url=audio_url[:60])

        # 7) Transcribir el audio
        text = await _transcribe_audio(audio_url)
        if not text:
            logger.warning("recaptcha_transcription_failed")
            return False

        logger.info("recaptcha_transcription_success", text=text)

        # 8) Ingresar la respuesta y verificar
        await bframe.evaluate(
            f"() => {{ const i=document.querySelector('#audio-response'); if(i) i.value={repr(text.strip().lower())}; }}"
        )
        await page.wait_for_timeout(300)

        verified = await bframe.evaluate(
            "() => { const b=document.querySelector('#recaptcha-verify-button'); if(b){b.click();return true;} return false; }"
        )
        await page.wait_for_timeout(3000)

        if not verified:
            return False

        # 9) Confirmar exito
        solved = await anchor_frame.evaluate(
            "() => { const c=document.querySelector('#recaptcha-anchor'); return c ? c.getAttribute('aria-checked') : 'not_found'; }"
        )
        if solved == "true":
            logger.info("recaptcha_solved_via_audio")
            return True

        logger.warning("recaptcha_solve_failed")
        return False

    except Exception as exc:
        logger.warning("recaptcha_solve_exception", exc=str(exc))
        return False


async def _close_modal(page) -> None:
    """Cierra el modal de advertencia de TCC si esta visible."""
    try:
        await page.evaluate("""
            () => {
                const btns = document.querySelectorAll('button, p, span');
                for (const btn of btns) {
                    if (btn.textContent.trim().toUpperCase() === 'ACEPTAR') {
                        btn.click();
                        return;
                    }
                }
                const overlay = document.querySelector('[class*=Overlay]');
                if (overlay) overlay.click();
            }
        """)
    except Exception:
        pass


def _find_frame(page, keyword: str):
    for frame in page.frames:
        if keyword in frame.url:
            return frame
    return None


async def _transcribe_audio(audio_url: str) -> str | None:
    try:
        import httpx
        import speech_recognition as sr
        from pydub import AudioSegment
        import imageio_ffmpeg

        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as c:
            r = await c.get(audio_url, headers={"Referer": "https://www.google.com/"})
            if r.status_code != 200:
                logger.warning("audio_download_failed", status=r.status_code)
                return None
            audio_data = r.content

        AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

        tmp = Path(tempfile.gettempdir())
        mp3_path = tmp / "tcc_captcha.mp3"
        wav_path = tmp / "tcc_captcha.wav"

        with open(str(mp3_path), "wb") as f:
            f.write(audio_data)

        audio_seg = AudioSegment.from_mp3(str(mp3_path))
        audio_seg.export(str(wav_path), format="wav")

        recognizer = sr.Recognizer()
        with sr.AudioFile(str(wav_path)) as source:
            audio_content = recognizer.record(source)

        try:
            os.remove(str(mp3_path))
            os.remove(str(wav_path))
        except Exception:
            pass

        text = recognizer.recognize_google(audio_content, language="en-US")
        return text

    except Exception as exc:
        logger.warning("transcribe_exception", exc=str(exc))
        return None
