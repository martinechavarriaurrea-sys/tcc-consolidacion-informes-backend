# Guía de configuración — Envío de email via Microsoft Graph API

Esta guía explica cómo registrar la aplicación en Azure AD y configurar
los secrets en GitHub para que el sistema envíe correos sin depender de
Outlook Desktop ni de ningún equipo local.

---

## Resumen del mecanismo

El sistema usa el flujo **client_credentials** de OAuth 2.0:
- La aplicación se registra en Azure AD con permiso `Mail.Send` (Application)
- Con `client_id` + `client_secret` + `tenant_id` obtiene un token de Graph API
- Usa ese token para llamar `POST /v1.0/users/{sender}/sendMail`
- No hay usuario ni login interactivo — funciona desde GitHub Actions sin intervención humana

---

## Paso 1 — Registrar la aplicación en Azure Portal

1. Ir a [https://portal.azure.com](https://portal.azure.com) e iniciar sesión con la cuenta administradora de la organización.
2. Buscar **"Azure Active Directory"** en la barra de búsqueda y abrirlo.
3. En el menú lateral, ir a **"Registros de aplicaciones"** → **"Nuevo registro"**.
4. Completar el formulario:
   - **Nombre:** `TCC-Email-Sender` (o el nombre que prefieras)
   - **Tipos de cuenta admitidos:** "Cuentas solo en este directorio organizativo"
   - **URI de redirección:** dejar vacío (no aplica para client_credentials)
5. Clic en **"Registrar"**.

---

## Paso 2 — Anotar client_id y tenant_id

Después de registrar, en la pantalla de resumen de la app:

- **Id. de aplicación (cliente)** → este es `AZURE_CLIENT_ID`
- **Id. de directorio (inquilino)** → este es `AZURE_TENANT_ID`

Copiar ambos valores.

---

## Paso 3 — Asignar permiso Mail.Send (Application permission)

1. En el menú lateral de la app, ir a **"Permisos de API"**.
2. Clic en **"Agregar un permiso"** → **"Microsoft Graph"** → **"Permisos de aplicación"**.
3. Buscar `Mail.Send` y marcarlo.
4. Clic en **"Agregar permisos"**.
5. Clic en **"Conceder consentimiento de administrador para [organización]"** → confirmar.

> **Importante:** Sin el consentimiento de administrador, el token se obtiene pero
> el envío falla con HTTP 403. Es obligatorio hacer este paso.

---

## Paso 4 — Generar el client secret

1. En el menú lateral de la app, ir a **"Certificados y secretos"**.
2. Clic en **"Nuevo secreto de cliente"**.
3. Descripción: `GitHub Actions TCC` (o cualquier nombre)
4. Expiración: recomendado **24 meses** (anotar la fecha de expiración para renovar antes)
5. Clic en **"Agregar"**.
6. **Copiar el valor del secreto INMEDIATAMENTE** — solo se muestra una vez.

Este valor es `AZURE_CLIENT_SECRET`.

---

## Paso 5 — Verificar que el remitente tiene licencia M365

El campo `SENDER_EMAIL` debe ser un usuario de tu organización con licencia Microsoft 365
que incluya Exchange Online. Sin licencia activa, Graph API retorna HTTP 400.

Para verificar:
1. Ir a **Microsoft 365 Admin Center** → **Usuarios activos**
2. Buscar el usuario y confirmar que tiene una licencia M365 asignada

---

## Paso 6 — Agregar los 5 secrets en GitHub

1. Ir al repositorio en GitHub → **Settings** → **Secrets and variables** → **Actions**
2. Clic en **"New repository secret"** y agregar uno por uno:

   | Nombre del secret | Valor |
   |-------------------|-------|
   | `AZURE_CLIENT_ID` | Id. de aplicación del paso 2 |
   | `AZURE_CLIENT_SECRET` | Valor del secreto del paso 4 |
   | `AZURE_TENANT_ID` | Id. de directorio del paso 2 |
   | `SENDER_EMAIL` | Correo del remitente, ej: `martin@asteco.com.co` |
   | `RECIPIENT_EMAILS` | Destinatarios separados por coma, ej: `adiaz@asteco.com.co,bvillada@asteco.com.co` |

---

## Paso 7 — Validar que funciona antes de desactivar Windows

Ejecutar el script localmente con las variables de entorno configuradas:

```bash
# En bash (Linux/Mac/Git Bash en Windows)
export AZURE_CLIENT_ID="xxxx-xxxx-xxxx"
export AZURE_CLIENT_SECRET="tu-secreto-aqui"
export AZURE_TENANT_ID="yyyy-yyyy-yyyy"
export SENDER_EMAIL="martin@asteco.com.co"
export RECIPIENT_EMAILS="adiaz@asteco.com.co,bvillada@asteco.com.co"
export BACKEND_URL="https://tcc-consolidacion-informes-backend.vercel.app"
export CRON_TOKEN="un-token-valido-del-backend"  # usar settings.cron_secret del backend

python scripts/send_email_graph.py --cycle 0700
```

```powershell
# En PowerShell (Windows)
$env:AZURE_CLIENT_ID = "xxxx-xxxx-xxxx"
$env:AZURE_CLIENT_SECRET = "tu-secreto-aqui"
$env:AZURE_TENANT_ID = "yyyy-yyyy-yyyy"
$env:SENDER_EMAIL = "martin@asteco.com.co"
$env:RECIPIENT_EMAILS = "adiaz@asteco.com.co,bvillada@asteco.com.co"
$env:BACKEND_URL = "https://tcc-consolidacion-informes-backend.vercel.app"
$env:CRON_TOKEN = "un-token-valido-del-backend"

python scripts/send_email_graph.py --cycle 0700
```

**Salidas esperadas:**

- `no_pending_report cycle=0700` → no hay reporte pendiente (normal si no se acaba de ejecutar un ciclo)
- `graph_email_sent sender=... recipients=...` → envío exitoso
- `report_marked_sent id=...` → marcado en backend

---

## Comportamiento de idempotencia

El script es seguro para re-ejecutar:

| Situación | Comportamiento |
|-----------|---------------|
| No hay reporte para el ciclo | Termina con exit 0, sin error |
| Reporte ya fue enviado | El backend retorna 404 → termina con exit 0 |
| Envío exitoso | Marca como enviado → re-ejecución termina con exit 0 |
| Falla al enviar | Lanza excepción → el step de Actions queda en rojo (visible en logs) |
| Falla al marcar_sent | Lanza excepción, pero el email ya fue enviado → en el siguiente intento el backend retorna 404 |

---

## Renovación del client secret

Los client secrets de Azure AD tienen fecha de expiración. Para renovar:

1. Ir a Azure Portal → App registrations → TCC-Email-Sender → Certificados y secretos
2. Agregar un nuevo secreto (no eliminar el anterior hasta actualizar GitHub)
3. Actualizar `AZURE_CLIENT_SECRET` en GitHub Secrets
4. Verificar que el workflow funcione
5. Eliminar el secreto anterior en Azure

**Recomendación:** Poner un recordatorio de calendario 1 mes antes de la fecha de expiración.

---

## Troubleshooting

| Error | Causa probable | Solución |
|-------|----------------|----------|
| HTTP 401 al obtener token | `client_id`, `client_secret` o `tenant_id` incorrectos | Verificar los valores en Azure Portal |
| HTTP 403 al enviar | Permiso `Mail.Send` sin consentimiento de admin | Repetir el paso 3 de esta guía |
| HTTP 400 al enviar | `SENDER_EMAIL` sin licencia M365 | Verificar licencia del usuario en M365 Admin |
| HTTP 404 del backend | Reporte ya enviado o no generado aún | Normal — el script sale limpiamente |
| `provider_not_configured` | Variables de entorno vacías | Verificar que los secrets están bien en GitHub |
