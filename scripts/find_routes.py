import asyncio
import re
import sys
sys.path.insert(0, ".")
import httpx


async def main():
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
        r = await c.get("https://tcc.com.co/wp-content/themes/tcc-theme/dist/common.3e4d0cfc.chunk.js")
        js = r.text

        # Buscar rastreo domestic
        for kw in ["rastreoCourier", "rastreoMensajeria", "consultarRastreo", "trazabilidadCourier",
                   "consultarGuia", "buscarGuia", "getGuia", "seekGuide", "getTracking", "rastreoNacional"]:
            idx = js.lower().find(kw.lower())
            if idx >= 0:
                print(f"\n=== {kw} at {idx} ===")
                print(js[max(0, idx-100):idx+400])

        # Todos los routes con keywords de tracking
        print("\n=== ALL TRACKING ROUTES ===")
        for m in re.finditer(r'route:\s*["\']([^"\']+)["\']', js):
            route = m.group(1)
            if any(x in route.lower() for x in ["rastreo", "courier", "track", "guia", "envio", "seek", "status"]):
                print(" ", route)

        # Buscar u.Z en el codigo del formulario
        idx = js.find("u.Z.getTracking")
        while idx >= 0:
            print(f"\n=== u.Z.getTracking at {idx} ===")
            print(js[max(0, idx-500):idx+500])
            idx = js.find("u.Z.getTracking", idx+1)


asyncio.run(main())
