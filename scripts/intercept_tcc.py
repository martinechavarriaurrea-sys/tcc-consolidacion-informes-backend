"""
Usa Playwright para interceptar TODOS los requests de red cuando se envia
el formulario de rastreo de TCC. Captura el endpoint real y su estructura.
"""
import asyncio
import sys
sys.path.insert(0, ".")

from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        all_requests = []
        all_responses = []

        async def on_request(req):
            all_requests.append({
                "method": req.method,
                "url": req.url,
                "post_data": req.post_data,
                "headers": dict(req.headers),
            })

        async def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if "json" in ct or "text" in ct:
                    body = await resp.text()
                    if len(body) > 20:
                        all_responses.append({
                            "url": resp.url,
                            "status": resp.status,
                            "body": body[:2000],
                        })
            except Exception:
                pass

        page.on("request", on_request)
        page.on("response", on_response)

        print("Loading TCC tracking page...")
        await page.goto(
            "https://tcc.com.co/courier/mensajeria/rastrear-envio/",
            wait_until="networkidle",
            timeout=45000,
        )
        await page.wait_for_timeout(2000)

        # Limpiar para solo capturar lo que pasa al enviar
        all_requests.clear()
        all_responses.clear()

        print("Filling form...")
        textarea = await page.query_selector("textarea")
        if textarea:
            await textarea.fill("370029693")
            await page.wait_for_timeout(300)

        buscar_btn = await page.query_selector("[class*=submitForm]")
        if buscar_btn:
            print("Clicking BUSCAR...")
            await buscar_btn.click()
            await page.wait_for_timeout(8000)

        print(f"\nTotal requests after submit: {len(all_requests)}")
        for req in all_requests:
            skip = any(x in req["url"] for x in ["google", "linkedin", "facebook", "doubleclick", "analytics"])
            if not skip:
                print(f"\n{req['method']} {req['url']}")
                if req["post_data"]:
                    print(f"  POST: {str(req['post_data'])[:300]}")

        print(f"\nTotal JSON/text responses: {len(all_responses)}")
        for resp in all_responses:
            skip = any(x in resp["url"] for x in ["google", "linkedin", "facebook", "doubleclick", "analytics", "activecampaign"])
            if not skip:
                print(f"\n{resp['status']} {resp['url']}")
                print(f"  {resp['body'][:400]}")

        await browser.close()


asyncio.run(main())
