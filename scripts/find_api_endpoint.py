"""
Analiza el bundle de React de TCC para encontrar el endpoint de tracking.
Busca el handler del formulario SeekGuide y los llamados fetch/axios.
"""
import asyncio
import re
import sys

sys.path.insert(0, ".")
import httpx


async def main():
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
        r = await c.get("https://tcc.com.co/wp-content/themes/tcc-theme/dist/app.bundle.js?ver=1.0.1")
        js = r.text
        print(f"Bundle size: {len(js):,} chars")

        # Buscar el componente SeekGuide y su contexto (5000 chars antes y despues)
        idx = js.find("SeekGuide-module__submitForm")
        if idx >= 0:
            snippet = js[max(0, idx-5000):idx+5000]

            # Buscar fetch/axios/XMLHttpRequest
            for kw in ["fetch(", "axios.", ".post(", ".get(", "XMLHttpRequest", "wp-json", "admin-ajax", "apiUrl", "baseUrl", "endpoint"]:
                pos = snippet.find(kw)
                if pos >= 0:
                    print(f"\n[{kw}] at offset {pos}:")
                    print(snippet[max(0,pos-100):pos+300])

        # Buscar en todo el bundle por patrones de URL de tracking
        print("\n\n=== ALL URL PATTERNS ===")
        # Buscar strings que parezcan endpoints de API
        patterns = [
            r'"(/[a-zA-Z0-9_/-]+(?:tracking|guia|rastrear|envio|seek|courier)[a-zA-Z0-9_/-]*)"',
            r"'(/[a-zA-Z0-9_/-]+(?:tracking|guia|rastrear|envio|seek|courier)[a-zA-Z0-9_/-]*)'",
            r'`(/[a-zA-Z0-9_/-]+(?:tracking|guia|rastrear|envio|seek|courier)[a-zA-Z0-9_/-]*)`',
        ]
        found = set()
        for pat in patterns:
            for m in re.finditer(pat, js, re.IGNORECASE):
                url = m.group(1)
                if url not in found:
                    found.add(url)
                    print(" ", url)

        # Buscar llamadas axios o fetch con template literals
        print("\n\n=== FETCH/AXIOS CALLS ===")
        for pat in [
            r'\.post\s*\(\s*[`"\']([^`"\']+)[`"\']',
            r'\.get\s*\(\s*[`"\']([^`"\']+)[`"\']',
            r'fetch\s*\(\s*[`"\']([^`"\']+)[`"\']',
        ]:
            for m in re.finditer(pat, js, re.IGNORECASE):
                url = m.group(1)
                if url.startswith("/") or url.startswith("http"):
                    print(f"  {url[:100]}")


asyncio.run(main())
