# Integración TCC — Documentación técnica

## Estado actual (corte: 2026-04-22)

### Lo que se confirmó
- Existe página pública de rastreo en `https://tcc.com.co/courier/mensajeria/rastrear-envio/`
- El parámetro de consulta confirmado por configuración es `guia` (ej. `?guia=123456789`)
- Existe endpoint WordPress REST API en `/wp-json/` (sitio construido sobre WordPress)
- **No se confirmó una API pública oficial de tracking** para casos de uso programático

### Riesgo crítico confirmado: página JavaScript-rendered

La página de rastreo de TCC muy probablemente carga los resultados vía JavaScript
(comportamiento típico de sitios WordPress con componentes React/Vue). Esto significa:

- Un HTTP GET simple con `httpx` puede devolver la shell HTML sin datos de tracking
- Si `parse_error` o `empty_response` aparecen con frecuencia en producción, la causa
  más probable es que los datos de tracking se cargan por AJAX/XHR después del HTML inicial
- **Esto no rompe el sistema**: el proveedor devuelve `fetch_success=False` con el
  código de error correcto, y el ciclo de tracking continúa para las demás guías

### Plan de acción si httpx no extrae datos en producción

1. Verificar manualmente `payload_snapshot.html_excerpt` en un registro real
2. Si el excerpt no contiene datos de tracking → la página es JS-rendered
3. Activar Playwright (ya incluido en `requirements.txt`):
   - Implementar `TCCPlaywrightProvider` siguiendo la misma interfaz `TrackingProvider`
   - Activarlo via `TCC_INTEGRATION_MODE=auto` con el web provider como fallback
4. Alternativa: coordinar con TCC para acceso a API oficial (ver sección al final)

---

## Objetivo de diseño

Evitar acoplamiento a una sola fuente de datos y exponer un contrato uniforme al backend.
El sistema es resiliente a cambios de proveedor sin modificar la capa de negocio.

### Payload uniforme de salida (TrackingResult)

| Campo | Tipo | Nullable |
|-------|------|----------|
| `tracking_number` | str | No |
| `current_status_raw` | str | Sí |
| `current_status_normalized` | str | Sí |
| `current_status_at` | datetime | Sí |
| `destination` | str | Sí |
| `package_type` | str | Sí |
| `client_name` | str | Sí |
| `events` | list[TrackingEventData] | No (lista vacía) |
| `payload_snapshot` | dict | No (dict vacío) |
| `fetch_success` | bool | No |
| `fetch_error` | str | Sí |

### Cada evento (TrackingEventData)

| Campo | Tipo | Nullable |
|-------|------|----------|
| `status_raw` | str | No |
| `status_normalized` | str | No |
| `event_at` | datetime | Sí |
| `observed_at` | datetime | No |
| `notes` | str | Sí |

---

## Componentes implementados

| Archivo | Clase/función | Rol |
|---------|--------------|-----|
| `app/integrations/tcc/base.py` | `TrackingProvider`, `TrackingResult`, `UpstreamTransientError` | Contrato base |
| `app/integrations/tcc/scraper.py` | `TCCWebProvider` | Proveedor HTTP + parser |
| `app/integrations/tcc/api_provider.py` | `TCCApiProvider` | Proveedor REST API |
| `app/integrations/tcc/client.py` | `get_tcc_client`, `FailoverTrackingProvider` | Resolver y failover |
| `app/integrations/tcc/parser.py` | `parse_tracking_response` | Parser HTML multi-estrategia |
| `app/utils/status_normalizer.py` | `normalize_status` | Normalización de estados |

---

## Selección de proveedor

Variable de entorno: `TCC_INTEGRATION_MODE=web|api|auto`

| Modo | Comportamiento |
|------|---------------|
| `web` | Solo `TCCWebProvider` (default de producción) |
| `api` | `TCCApiProvider`; si `TCC_ENABLE_WEB_FALLBACK=true`, activa failover a web |
| `auto` | API si `TCC_API_BASE_URL` + `TCC_API_KEY` configurados, de lo contrario web |

---

## Parser HTML — estrategias de extracción

El parser `parse_tracking_response()` aplica estrategias en orden hasta extraer eventos:

1. **`table`** — Detecta tablas HTML con columnas de estado/fecha/observación
2. **`semantic`** — Busca elementos con clases CSS que contienen "track", "estado", "event", "timeline"
3. **`script`** — Parsea `<script type="application/ld+json">` o extrae patrones `"estado": "..."` de scripts inline
4. **`text_pattern`** — Regex sobre texto plano buscando `Estado: <valor>`
5. **`keyword_fallback`** — Extrae la primera línea de texto que contenga keyword de estado (registr, recogid, transit, etc.)

Si ninguna estrategia produce eventos, el resultado tiene `partial_structure=True` y el sistema no falla.

### Señales de diagnóstico en TrackingParseResult

| Campo | Significado |
|-------|-------------|
| `blocked` | Se detectaron patrones de captcha/cloudflare |
| `invalid_tracking` | TCC indicó que la guía no existe |
| `empty_response` | HTML vacío o en blanco |
| `partial_structure` | Hubo HTML pero no se extrajeron eventos de forma confiable |
| `strategy_used` | Primera estrategia que produjo eventos |
| `parser_warnings` | Lista de advertencias acumuladas |

---

## Robustez implementada

### TCCWebProvider

- Timeout configurable (`TCC_REQUEST_TIMEOUT`, default 30s)
- Retry con backoff exponencial (`TCC_MAX_RETRIES=3`, `TCC_RETRY_DELAY=2.0`)
- Retries solo en: `TimeoutException`, `NetworkError`, `UpstreamTransientError` (5xx)
- Sin retry en: captcha, guía inválida, parse_error (evita spam al servidor)
- Detección de bloqueo/captcha antes de intentar parsear
- Manejo de respuesta vacía/truncada con `TCC_MIN_HTML_LENGTH=300`
- Parser desacoplado y reemplazable sin tocar el proveedor

### TCCApiProvider

- No lanza excepción si no hay configuración: devuelve `provider_not_configured`
- Tracking path configurable (`TCC_API_TRACKING_PATH`)
- Normalización defensiva: acepta claves en español e inglés
- Fallback mínimo: si API da estado actual sin historial, crea un evento sintético
- Retry con backoff en errores transitorios

### FailoverTrackingProvider

- Intenta proveedor primario; si falla, ejecuta fallback
- No hace fallback en `invalid_tracking_number` (evita consultar dos fuentes innecesariamente)
- Anota en `payload_snapshot` el motivo del fallback y el proveedor de origen
- `health_check()` usa primario si está disponible, fallback si no

---

## Taxonomía de errores (`fetch_error`)

| Código | Causa | ¿Hay retry? |
|--------|-------|------------|
| `provider_not_configured` | Variables de API no definidas | No |
| `network_error` | DNS, TLS, conexión rechazada | Sí |
| `timeout` | Latencia excesiva o servidor lento | Sí |
| `upstream_error` | HTTP 5xx del servidor TCC | Sí |
| `captcha_or_blocked` | Protección anti-bot activa | No |
| `empty_response` | HTML vacío (posible JS-rendered) | No* |
| `invalid_tracking_number` | Guía no existe en TCC | No |
| `parse_error` | HTML presente pero no se pudo extraer datos | No |

*`empty_response` se retryea si el HTML es menor a `TCC_MIN_HTML_LENGTH` (respuesta truncada).
Si el HTML es vacío después del retry, se clasifica como `empty_response` final.

---

## Asunciones explícitas

1. TCC puede cambiar su estructura HTML sin previo aviso — el parser multi-estrategia mitiga esto.
2. El frontend **no depende** de `destination`, `package_type` ni `client_name` para registrar guías. Estos campos son informativos y siempre son `nullable`.
3. Si hay datos parciales, el sistema prioriza devolver al menos un evento antes que fallar el proceso completo.
4. Si la API oficial de TCC aparece, se activa por configuración sin cambiar la capa de negocio (solo configurar `TCC_API_*` y cambiar `TCC_INTEGRATION_MODE`).
5. La concurrencia máxima de consultas a TCC está controlada por `TrackingService` (semáforo de 5 requests simultáneos), no en el proveedor.

---

## Puntos pendientes para cierre total de integración API

Estos puntos requieren coordinación externa con TCC:

| Pendiente | Impacto | Cómo activarlo cuando esté disponible |
|-----------|---------|--------------------------------------|
| Contrato oficial de endpoint de tracking | Alto | Configurar `TCC_API_BASE_URL` y `TCC_API_TRACKING_PATH` |
| Mecanismo de autenticación definitivo | Alto | Configurar `TCC_API_KEY` y `TCC_API_AUTH_SCHEME` |
| Límites de consumo (rate limiting) | Medio | Ajustar concurrencia en `TrackingService` |
| Política anti-bot (whitelisting de IP) | Medio | Coordinar con TCC para whitelist del servidor de producción |
| Validación de estructura real del HTML | Alto | Ejecutar en producción y revisar `payload_snapshot` |

**Operación recomendada mientras no exista contrato API:**
`TCC_INTEGRATION_MODE=web` con `TCC_ENABLE_WEB_FALLBACK=true`

---

## Cobertura de tests

| Módulo | Tests | Estrategias cubiertas |
|--------|-------|-----------------------|
| `test_parser.py` | ~35 | date parsing, tablas, semántico, script, text_pattern, fallback, deduplicación, metadata, señales de diagnóstico, Unicode, HTML malformado |
| `test_tcc_providers.py` | ~30 | TCCWebProvider éxito/errores, TCCApiProvider no-configurado/éxito/invalid, FailoverTrackingProvider todos los escenarios, contrato TrackingResult |
| `test_normalizer.py` | ~4 | Normalización, terminales, issues |

Ejecutar tests de integración TCC aislados:
```bash
pytest tests/test_parser.py tests/test_tcc_providers.py tests/test_normalizer.py -v
```
