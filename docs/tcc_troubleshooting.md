# Troubleshooting — Integración TCC

## 1. `fetch_error=provider_not_configured`

**Causa probable:** Modo `api` activo sin `TCC_API_BASE_URL` o `TCC_API_KEY`.

**Acciones:**
- Cambiar a `TCC_INTEGRATION_MODE=web` o `auto`
- Completar `TCC_API_*` si ya existe contrato oficial con TCC

---

## 2. `fetch_error=timeout`

**Causa probable:** Latencia alta o degradación temporal del sitio TCC.

**Acciones:**
- Aumentar `TCC_REQUEST_TIMEOUT` (ej. 30 → 45)
- Verificar salida de red del servidor de producción
- Revisar que no haya restricciones de firewall o proxy corporativo
- Si el timeout es persistente (> 3 ciclos consecutivos), puede indicar que TCC está bloqueando la IP

---

## 3. `fetch_error=network_error`

**Causa probable:** DNS, TLS, conectividad o bloqueo de red.

**Acciones:**
- Probar conectividad: `curl -I https://tcc.com.co` desde el host del backend
- Validar certificados TLS y configuración de proxy corporativo
- Si el servidor está en cloud (AWS/GCP/Render), verificar si TCC bloquea rangos de IP de proveedores cloud

---

## 4. `fetch_error=captcha_or_blocked`

**Causa probable:** TCC activó protección anti-bot (Cloudflare, reCAPTCHA u otro).

**Acciones:**
- Reducir concurrencia de consultas (parámetro `semaphore` en `TrackingService`)
- Revisar si la IP del servidor está en listas negras
- Coordinar con TCC para whitelist de IP del servidor de producción
- Considerar rotar User-Agent o añadir headers adicionales en `scraper.py._HEADERS`
- Si persiste, evaluar activar `TCCApiProvider` con contrato oficial

---

## 5. `fetch_error=empty_response` frecuente

**Causa probable — Alta probabilidad:** La página de rastreo de TCC es **JavaScript-rendered**.

El HTML inicial que devuelve el servidor no contiene los datos de tracking. Los resultados
se cargan mediante AJAX/XHR después que el JS del navegador ejecuta. `httpx` solo obtiene
la shell HTML, no los datos.

**Cómo verificar:**
1. Revisar `payload_snapshot.html_excerpt` en los logs de un `TrackingRun` fallido
2. Si el excerpt solo contiene la estructura base del sitio (menú, header, footer) sin tablas
   de tracking → la página es JS-rendered

**Solución definitiva:**
```
# Opción A: Implementar TCCPlaywrightProvider
# playwright ya está en requirements.txt
# Crear app/integrations/tcc/playwright_provider.py implementando TrackingProvider
# Activar con TCC_INTEGRATION_MODE=playwright (añadir modo al resolver en client.py)

# Opción B: Encontrar el endpoint AJAX que llama el JS del portal
# Usar DevTools → Network tab → filtrar XHR mientras se rastrea una guía en el navegador
# Ese endpoint puede configurarse directamente en TCCApiProvider
```

---

## 6. `fetch_error=invalid_tracking_number`

**Causa probable:** Guía inexistente, mal digitada o no disponible en TCC.

**Acciones:**
- Verificar formato de guía en origen (longitud, caracteres, prefijos esperados)
- Confirmar manualmente la guía en `https://tcc.com.co/courier/mensajeria/rastrear-envio/`
- Si la guía existe manualmente pero el sistema la reporta inválida → posible diferencia de
  formato entre lo que el usuario ingresa y lo que TCC espera (mayúsculas, ceros iniciales)

---

## 7. `fetch_error=parse_error` o `partial_structure=True` frecuentes

**Causa probable:** Cambio de estructura HTML en el portal de TCC, o la página es JS-rendered.

**Acciones:**
1. Revisar `payload_snapshot.parser.strategy_used` — si es `None`, ninguna estrategia funcionó
2. Revisar `payload_snapshot.parser.warnings` para señales específicas
3. Revisar `payload_snapshot.html_excerpt` — ¿tiene el HTML algo recognocible de tracking?
4. Si el HTML tiene datos de tracking pero el parser no los extrae → actualizar el parser:
   ```bash
   # Identificar la nueva estructura
   # Actualizar selectores en parser.py
   # Ejecutar tests:
   pytest tests/test_parser.py -v
   ```
5. Si el HTML no tiene datos de tracking → ver ítem 5 (JS-rendered)

---

## 8. Fallback activado con frecuencia inesperada

**Síntoma:** Los logs muestran `tcc_provider_fallback_start` frecuentemente.

**Acciones:**
- Revisar `payload_snapshot.fallback_reason` en logs
- Si el primario es API: verificar vigencia de credenciales y disponibilidad del endpoint
- Si el primario es web y falla con `captcha_or_blocked`: ver ítem 4
- Si el primario falla con `empty_response`: ver ítem 5

---

## 9. Eventos duplicados en historial

**Síntoma:** La misma guía aparece con el mismo estado múltiples veces en la DB.

**Causa:** El parser devuelve duplicados que no fueron deduplicados, o el `TrackingService`
inserta eventos repetidos en ciclos consecutivos.

**Verificar:**
- El parser tiene deduplicación interna (`_dedupe_events`). Si falla, revisar si TCC cambió
  el formato de fecha o status que afecta la clave de deduplicación.
- El `TrackingService` no debe insertar eventos con `(shipment_id, status_normalized, event_at)`
  que ya existan en la DB. Verificar el repositorio.

---

## 10. Ciclo de tracking lento o con timeouts acumulados

**Síntoma:** Un ciclo diario tarda más de lo esperado; algunos shipments no se actualizan.

**Causa:** El semáforo de concurrencia es de 5 requests simultáneos. Si TCC está lento,
los timeouts se acumulan y el ciclo se extiende.

**Acciones:**
- Revisar `TrackingRun.shipments_failed` en la DB — si es alto, hay problemas de red/TCC
- Reducir `TCC_REQUEST_TIMEOUT` para fallar rápido si TCC no responde
- Revisar que `tcc_max_retries=3` no esté causando 3x el tiempo de espera por guía

---

## Comandos de diagnóstico rápido

```bash
# Ejecutar ciclo manual
python scripts/run_job.py --job tracking

# Ejecutar tests de integración TCC
pytest tests/test_parser.py tests/test_tcc_providers.py tests/test_normalizer.py -v

# Ver último TrackingRun en DB
# SELECT * FROM tracking_runs ORDER BY started_at DESC LIMIT 5;

# Ver guías que fallaron en último ciclo
# SELECT tracking_number, current_status, updated_at 
# FROM shipments 
# WHERE is_active = true AND updated_at < now() - interval '4 hours'
# ORDER BY updated_at ASC;
```
