"""
Prueba los endpoints de tracking domestico de TCC.
Encontrados en el bundle de React: /tracking/wid y /tracking/remesa
"""
import asyncio
import sys

sys.path.insert(0, ".")
import httpx

BASE = "https://tccrestify-dot-tcc-cloud.appspot.com"
GUIA = "370029693"

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://tcc.com.co",
    "Referer": "https://tcc.com.co/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9",
}


async def main():
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as c:
        # Probar todos los endpoints de tracking
        tests = [
            # (method, path, body)
            ("POST", "/tracking/wid", {"numDocTransporte": GUIA}),
            ("POST", "/tracking/wid", {"tccId": GUIA}),
            ("POST", "/tracking/wid", {"guia": GUIA}),
            ("POST", "/tracking/wid", {"numDocTransporte": GUIA, "captcha": "test"}),
            ("GET", f"/tracking/wid?numDocTransporte={GUIA}", None),
            ("GET", f"/tracking/wid?tccId={GUIA}", None),
            ("POST", "/tracking/remesa", {"numDocTransporte": GUIA}),
            ("POST", "/tracking/remesa", {"remesa": GUIA}),
            ("POST", "/tracking/remesa", {"guia": GUIA}),
            ("GET", f"/tracking/remesa?remesa={GUIA}", None),
            ("POST", "/tracking/estados", {}),
            ("GET", "/tracking/estados", None),
            ("GET", f"/tracking/widfile?numDocTransporte={GUIA}", None),
        ]

        for method, path, body in tests:
            try:
                if method == "POST":
                    r = await c.post(f"{BASE}{path}", json=body)
                else:
                    r = await c.get(f"{BASE}{path}")

                status = r.status_code
                text = r.text[:300]
                print(f"{method} {path}")
                if body:
                    print(f"  Body: {body}")
                print(f"  {status}: {text}")
                print()
            except Exception as e:
                print(f"{method} {path}: ERROR {e}")


asyncio.run(main())
