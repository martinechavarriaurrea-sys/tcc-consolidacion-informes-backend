"""
Busca la funcion getTracking domestica en todos los chunks de TCC.
"""
import asyncio
import re
import sys

sys.path.insert(0, ".")
import httpx

CHUNK_URLS = [
    "https://tcc.com.co/wp-content/themes/tcc-theme/dist/common.3e4d0cfc.chunk.js",
    "https://tcc.com.co/wp-content/themes/tcc-theme/dist/GroupForms.5928ef81.chunk.js",
    "https://tcc.com.co/wp-content/themes/tcc-theme/dist/app.bundle.js?ver=1.0.1",
]


async def main():
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
        for url in CHUNK_URLS:
            r = await c.get(url)
            js = r.text
            name = url.split("/")[-1][:30]
            print(f"\n{'='*60}")
            print(f"FILE: {name} ({len(js):,} chars)")

            # Buscar la definicion de getTracking para mensajeria/courier
            for kw in ["getTracking:", "getTracking=", "c.getTracking", "exports.getTracking"]:
                idx = js.find(kw)
                while idx >= 0:
                    snippet = js[max(0, idx-200):idx+600]
                    if any(x in snippet for x in ["/tracking", "captcha", "wid", "remesa", "numDoc"]):
                        print(f"\n  [{kw} at {idx}]")
                        print("  " + snippet[:600])
                    idx = js.find(kw, idx+1)

            # Buscar la ruta /tracking/wid en contexto de funcion
            for route in ["/tracking/wid", "/tracking/remesa", "/courier/tracking", "/rastreo/"]:
                idx = js.find(route)
                while idx >= 0:
                    snippet = js[max(0, idx-300):idx+400]
                    print(f"\n  [route {route} at {idx}]")
                    print("  " + snippet[:500])
                    idx = js.find(route, idx+1)
                    if idx > 800000:
                        break


asyncio.run(main())
