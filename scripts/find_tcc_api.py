import asyncio
import re
import sys
sys.path.insert(0, ".")
import httpx


async def main():
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
        r = await c.get("https://tcc.com.co/wp-content/themes/tcc-theme/dist/app.bundle.js?ver=1.0.1")
        js = r.text

        # Buscar el modulo SeekGuide
        idx = js.find("SeekGuide-module")
        snippet = js[max(0, idx - 3000):idx + 4000]

        urls = re.findall(r"[\"'`]((?:https?://|/)[a-zA-Z0-9/._\-?=&%+]+)[\"'`]", snippet)
        print("URLs en SeekGuide module:")
        for u in list(dict.fromkeys(urls))[:20]:
            print(" ", u)

        actions = re.findall(r"action[\"':\s]+([a-zA-Z_]{4,40})", snippet)
        print("\nActions:", list(dict.fromkeys(actions))[:10])

        # Buscar en todo el bundle endpoints con 'guia' o 'tracking'
        print("\nBundled endpoints con guia/tracking:")
        for m in re.finditer(r"[\"'`](https://[a-zA-Z0-9.\-/]+)[\"'`]", js):
            url = m.group(1)
            if any(x in url.lower() for x in ["guia", "track", "rastrear", "envio", "api"]):
                print(" ", url)

        # Buscar posibles API keys o tokens
        print("\nAPI tokens/keys:")
        for m in re.finditer(r"(?:apiKey|api_key|token|bearer|authorization)[\"'\s:=]+([a-zA-Z0-9\-_]{20,80})", js, re.IGNORECASE):
            print(" ", m.group()[:100])

        # Buscar llamadas directas a wp-json
        print("\nwp-json calls:")
        for m in re.finditer(r"wp-json[a-zA-Z0-9/\-?=&_]{3,80}", js):
            print(" ", m.group()[:100])


asyncio.run(main())
