# Recomendaciones de operación — Integración TCC

## Configuración base para producción

```env
TCC_INTEGRATION_MODE=web
TCC_ENABLE_WEB_FALLBACK=true
TCC_BASE_URL=https://tcc.com.co
TCC_TRACKING_URL=https://tcc.com.co/courier/mensajeria/rastrear-envio/
TCC_TRACKING_QUERY_PARAM=guia
TCC_MIN_HTML_LENGTH=300
TCC_REQUEST_TIMEOUT=30
TCC_MAX_RETRIES=3
TCC_RETRY_DELAY=2.0
```

---

## Monitoreo mínimo recomendado

Revisar estas métricas después de cada ciclo de tracking (`TrackingRun`):

| Métrica | Umbral de alerta | Acción |
|---------|-----------------|--------|
| `fetch_success` rate | < 80% | Investigar errores dominantes en `fetch_error` |
| `captcha_or_blocked` | > 5% en 1 hora | Reducir concurrencia, revisar IP |
| `empty_response + parse_error` | > 10% en 1 hora | Verificar si TCC cambió estructura |
| `network_error + timeout` acumulados | 3 ciclos seguidos | Verificar red del servidor |

---

## Primer despliegue — checklist de validación

1. **Verificar conectividad al dominio TCC:**
   ```bash
   curl -I https://tcc.com.co
   ```

2. **Ejecutar un ciclo manual con una guía real:**
   ```bash
   python scripts/run_job.py --job tracking
   ```

3. **Revisar el resultado en logs y DB:**
   - ¿`fetch_success=True`? → httpx obtiene datos reales del portal
   - ¿`fetch_success=False` con `empty_response`? → La página es JS-rendered (ver nota crítica abajo)
   - ¿`fetch_success=False` con `parse_error`? → HTML con datos pero parser no los extrae

4. **Si `empty_response` en todas las guías:** ver procedimiento en `tcc_troubleshooting.md` ítem 5

---

## Nota crítica: riesgo de página JS-rendered

El portal de TCC está construido sobre WordPress y probablemente carga los datos de tracking
vía JavaScript/AJAX. Si esto se confirma en producción:

- `httpx` (cliente actual) no ejecuta JavaScript → no verá los datos de tracking
- El impacto es total: ninguna guía se actualizaría
- **Solución disponible:** `playwright` ya está en `requirements.txt`

### Ruta de escalada si se confirma JS-rendering

```
Día 0: Confirmar el problema revisando payload_snapshot.html_excerpt
Día 1: Usar DevTools del navegador para encontrar el endpoint AJAX de tracking
        → Si encontrado: configurar en TCCApiProvider directamente
        → Si no encontrado: implementar TCCPlaywrightProvider

Semana 1: Contactar a TCC para solicitar acceso a API oficial o whitelist de IP
```

---

## Mantenimiento preventivo

- **Cada vez que TCC anuncie cambios en su portal:** ejecutar test de parser con HTML de muestra real
- **Cada mes:** revisar tasa de `parse_error` y `partial_structure` en los últimos 30 días
- **Cada trimestre:** rotar credenciales si se usa API, verificar contrato vigente
- **En cada PR que toque la capa de tracking:** ejecutar
  ```bash
  pytest tests/test_parser.py tests/test_tcc_providers.py -v
  ```

---

## Plan de migración a API oficial (cuando TCC lo habilite)

1. Recibir de TCC: URL de endpoint, mecanismo de autenticación, documentación de payload

2. Configurar en `.env`:
   ```env
   TCC_API_BASE_URL=https://api.tcc.com.co
   TCC_API_KEY=tu-api-key
   TCC_API_TRACKING_PATH=/tracking/{tracking_number}
   TCC_API_AUTH_SCHEME=Bearer
   ```

3. Cambiar modo con fallback activado:
   ```env
   TCC_INTEGRATION_MODE=api
   TCC_ENABLE_WEB_FALLBACK=true
   ```

4. Observar 1-2 semanas comparando tasa de éxito API vs web en `TrackingRun`

5. Si API es estable (> 95% de éxito): desactivar fallback gradualmente
   ```env
   TCC_ENABLE_WEB_FALLBACK=false
   ```

6. Si el payload de la API tiene claves distintas a las esperadas: actualizar
   `TCCApiProvider._normalize_payload()` sin tocar la capa de negocio

---

## Riesgos operativos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| Portal JS-rendered (sin datos en httpx) | Alta | Total | Playwright o endpoint AJAX |
| Cambio de markup HTML sin aviso | Media | Alto | Parser multi-estrategia + tests en CI |
| Bloqueo anti-bot por volumen | Media | Alto | Whitelist IP con TCC + throttling |
| Dependencia de HTML sin SLA formal | Alta | Medio | Arquitectura desacoplada, API lista para activar |
| Cambio en parámetro `?guia=` | Baja | Total | Configurable en `TCC_TRACKING_QUERY_PARAM` |
