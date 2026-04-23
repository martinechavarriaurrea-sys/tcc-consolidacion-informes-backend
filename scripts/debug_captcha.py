"""
Debug completo del flujo de CAPTCHA en TCC.
"""
import asyncio
import sys

sys.path.insert(0, ".")
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="es-CO",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};"
        )
        page = await context.new_page()

        await page.goto(
            "https://tcc.com.co/courier/mensajeria/rastrear-envio/",
            wait_until="networkidle",
            timeout=45000,
        )
        await page.wait_for_timeout(1500)

        # Llenar guia
        textarea = await page.query_selector("textarea")
        if textarea:
            await textarea.fill("370029693")

        # Click BUSCAR
        await page.evaluate(
            '() => { const b=document.querySelector("[class*=submitForm]"); if(b) b.click(); }'
        )
        await page.wait_for_timeout(3000)

        # Cerrar modal si existe
        modal_closed = await page.evaluate("""
            () => {
                // Buscar boton Aceptar en el modal
                const btns = document.querySelectorAll('button, [class*=Aceptar], [class*=accept], [class*=close]');
                for (const btn of btns) {
                    const txt = btn.textContent.trim().toLowerCase();
                    if (txt === 'aceptar' || txt === 'ok' || txt === 'close' || txt === 'cerrar') {
                        btn.click();
                        return 'closed: ' + txt;
                    }
                }
                // Cerrar por overlay click
                const overlay = document.querySelector('[class*=Overlay]');
                if (overlay) { overlay.click(); return 'overlay_clicked'; }
                return 'no_modal_found';
            }
        """)
        print("Modal close result:", modal_closed)
        await page.wait_for_timeout(2000)

        # Ver estado de la pagina
        print("Frames after modal close:")
        for f in page.frames:
            print(f"  {f.url[:100]}")

        # Buscar el captcha widget visible
        captcha_visible = await page.evaluate("""
            () => {
                const iframes = document.querySelectorAll('iframe[src*=recaptcha]');
                return Array.from(iframes).map(f => ({
                    src: f.src.substring(0, 80),
                    visible: f.offsetParent !== null,
                    rect: JSON.stringify(f.getBoundingClientRect())
                }));
            }
        """)
        print("Captcha iframes:", captcha_visible)

        # Intentar click en el checkbox via JS en el frame
        anchor_frame = None
        for f in page.frames:
            if "anchor" in f.url:
                anchor_frame = f
                break

        if anchor_frame:
            print("Anchor frame found, trying JS click on checkbox...")
            result = await anchor_frame.evaluate("""
                () => {
                    const cb = document.querySelector('#recaptcha-anchor') || document.querySelector('.recaptcha-checkbox');
                    if (cb) { cb.click(); return 'clicked'; }
                    return document.body.innerHTML.substring(0, 300);
                }
            """)
            print("Checkbox click result:", result[:200])
            await page.wait_for_timeout(3000)

            # Ver si se resolvio
            checked = await anchor_frame.evaluate("""
                () => {
                    const cb = document.querySelector('#recaptcha-anchor');
                    if (cb) return cb.getAttribute('aria-checked');
                    return 'not found';
                }
            """)
            print("Checkbox aria-checked:", checked)
        else:
            print("No anchor frame found")

        # Ver bframe
        bframe = None
        for f in page.frames:
            if "bframe" in f.url:
                bframe = f
                break

        if bframe:
            print("bframe found, getting HTML...")
            html = await bframe.content()
            print("bframe HTML (first 1000):", html[:1000])
        else:
            print("No bframe found")

        await browser.close()


asyncio.run(main())
