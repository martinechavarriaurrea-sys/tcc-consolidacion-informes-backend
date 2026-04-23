# TCC-CONSOLIDACION-INFORMES

Sistema de seguimiento y consolidaciÃ³n automÃ¡tica de guÃ­as de transporte TCC para **ASTECO**.

---

## DescripciÃ³n

Monitorea guÃ­as TCC de forma automÃ¡tica 3 veces al dÃ­a. Por cada ciclo:

1. Consulta el estado actual de cada guÃ­a activa en TCC
2. Detecta cambios, novedades y entregas
3. Genera un reporte en **Excel** y **PDF** de formato corporativo
4. EnvÃ­a el reporte por correo a los destinatarios configurados
5. Detecta guÃ­as sin movimiento por mÃ¡s de 72 horas y alerta por separado

Los lunes a las 7 AM genera un **consolidado semanal** de la semana anterior.

---

## Arquitectura

```
TCC-CONSOLIDACION-INFORMES/
â”œâ”€â”€ app/
â”‚   â”œâ”€â”€ api/v1/          FastAPI routers (shipments, tracking, dashboard, reports)
â”‚   â”œâ”€â”€ core/            Config, DB, logging, exceptions
â”‚   â”œâ”€â”€ jobs/            Scheduler APScheduler + jobs (tracking_job.py)
â”‚   â”œâ”€â”€ models/          SQLAlchemy ORM models
â”‚   â”œâ”€â”€ repositories/    Acceso a BD por entidad
â”‚   â”œâ”€â”€ services/        LÃ³gica de negocio (tracking, alerts, excel, pdf, email)
â”‚   â””â”€â”€ utils/           date_utils, status_normalizer
â”œâ”€â”€ alembic/             Migraciones de BD
â”œâ”€â”€ tests/               Tests unitarios
â”œâ”€â”€ scripts/             run_job.py (ejecuciÃ³n manual)
â”œâ”€â”€ Dockerfile
â””â”€â”€ docker-compose.yml

frontend/                Next.js (dashboard)
reports/                 Archivos generados (Excel + PDF) â€” gitignored
```

---

## Integracion TCC (capa proveedor)

- Diseno tecnico: [docs/tcc_integration.md](docs/tcc_integration.md)
- Troubleshooting: [docs/tcc_troubleshooting.md](docs/tcc_troubleshooting.md)
- Recomendaciones operativas: [docs/tcc_operations.md](docs/tcc_operations.md)

---

## Horarios de ejecuciÃ³n (America/Bogota)

| Job                  | CuÃ¡ndo              | QuÃ© hace                                     |
|----------------------|---------------------|----------------------------------------------|
| Ciclo diario 07:00   | Todos los dÃ­as      | Consulta TCC + reporte Excel/PDF + email     |
| Ciclo diario 12:00   | Todos los dÃ­as      | Consulta TCC + reporte Excel/PDF + email     |
| Ciclo diario 16:00   | Todos los dÃ­as      | Consulta TCC + reporte Excel/PDF + email     |
| Consolidado semanal  | Lunes 07:00         | Resumen semana anterior + email              |
| VerificaciÃ³n alertas | Cada 30 min         | Detecta guÃ­as sin movimiento â‰¥72h            |

---

## Destinatarios de correo

| Tipo       | Destinatarios                                                |
|------------|--------------------------------------------------------------|
| Diario     | Angela Maria Diaz Cadavid, Bryan Villada                     |
| Semanal    | Juan Camilo MuÃ±oz                                            |
| Alertas    | Juan Camilo MuÃ±oz, Bryan Villada                             |

Para modificar: editar constantes en [app/services/email_service.py](app/services/email_service.py).

---

## Archivos de reporte generados

```
reports/
â”œâ”€â”€ diario/
â”‚   â”œâ”€â”€ reporte_tcc_diario_2026-04-22_0700.xlsx
â”‚   â”œâ”€â”€ reporte_tcc_diario_2026-04-22_0700.pdf
â”‚   â””â”€â”€ ...
â””â”€â”€ semanal/
    â”œâ”€â”€ reporte_tcc_semanal_2026-04-13_al_2026-04-18.xlsx
    â””â”€â”€ reporte_tcc_semanal_2026-04-13_al_2026-04-18.pdf
```

---

## Arranque local (sin Docker)

### Requisitos

- Python 3.12+
- PostgreSQL 14+
- Node.js 20+ (solo frontend)

### Pasos

```bash
# 1. Entorno virtual
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/Mac

# 2. Dependencias
pip install -r requirements.txt
python -m playwright install chromium   # Opcional: solo para validaciones de navegador

# 3. ConfiguraciÃ³n
cp .env.example .env
# Editar .env con: DATABASE_URL, SMTP_*, etc.

# 4. Migraciones
alembic upgrade head

# 5. Seed inicial
python scripts/seed.py

# 6. Arrancar
uvicorn app.main:app --reload --port 8000
```

- Swagger: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

---

## Arranque con Docker

```bash
# Copiar y completar configuraciÃ³n
cp .env.example .env

# Levantar DB + backend
docker-compose up --build -d

# Migraciones (primera vez)
docker-compose --profile migrate up migrate

# Ver logs
docker-compose logs -f backend

# Con frontend
docker-compose --profile full up --build -d
```

---

## EjecuciÃ³n manual de jobs

```bash
# Desde terminal (requiere .env configurado)
python scripts/run_job.py daily 0700
python scripts/run_job.py daily 1200
python scripts/run_job.py daily 1600
python scripts/run_job.py weekly
python scripts/run_job.py alerts
```

Desde la API (trigger en background, retorna 202):

```
POST /api/v1/reports/trigger/daily_0700
POST /api/v1/reports/trigger/weekly
POST /api/v1/reports/trigger/alerts
```

---

## ExportaciÃ³n manual de reportes

```
GET /api/v1/reports/daily?format=xlsx
GET /api/v1/reports/daily?format=pdf
GET /api/v1/reports/weekly?format=xlsx
GET /api/v1/reports/weekly?week_of=2026-04-13&format=pdf
GET /api/v1/reports/history?report_type=daily&limit=20
```

---

## Tests

```bash
pytest                                   # Todos
pytest tests/test_excel_service.py -v
pytest tests/test_pdf_service.py -v
pytest tests/test_alert_logic.py -v
pytest tests/test_email_service.py -v
pytest tests/test_tracking_job.py -v
pytest --cov=app --cov-report=term-missing
```

---

## PolÃ­tica de alertas 72 horas

- **CuÃ¡ndo:** guÃ­a activa sin cambio de estado por â‰¥72 horas
- **Anti-spam:** solo se crea alerta si no hay una abierta para la misma guÃ­a
- **ResoluciÃ³n automÃ¡tica:** cuando la guÃ­a vuelve a tener movimiento
- **Referencia:** [app/services/alert_service.py](app/services/alert_service.py)

---

## Variables de entorno clave

| Variable                        | DescripciÃ³n                            | Default             |
|---------------------------------|----------------------------------------|---------------------|
| `DATABASE_URL`                  | PostgreSQL async                       | (requerido)         |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` | ConfiguraciÃ³n SMTP           | (requerido)         |
| `ALERT_NO_MOVEMENT_HOURS`       | Umbral de alerta en horas              | `72`                |
| `ALERT_CHECK_INTERVAL_MINUTES`  | Frecuencia de verificaciÃ³n de alertas  | `30`                |
| `REPORTS_OUTPUT_DIR`            | Directorio de archivos generados       | `./reports`         |
| `EMAIL_MAX_RETRIES`             | Reintentos SMTP                        | `3`                 |
| `TCC_INTEGRATION_MODE`          | `web`, `api` o `auto`                  | `web`               |

Ver [.env.example](.env.example) para la lista completa.

---

## Despliegue en nube

### OpciÃ³n A â€” Railway / Render (mÃ¡s rÃ¡pido)

1. Push a GitHub
2. Conectar repo en Railway/Render
3. Configurar env vars en el panel
4. Agregar PostgreSQL como addon
5. El scheduler corre dentro del mismo proceso uvicorn â€” no requiere worker separado

### OpciÃ³n B â€” AWS ECS / GCP Cloud Run

1. Build y push a ECR / Artifact Registry
2. Task Definition con las env vars
3. RDS (AWS) o Cloud SQL (GCP) para PostgreSQL
4. Volumen de reportes: montar S3/GCS o usar EFS

### OpciÃ³n C â€” VPS con Docker Compose

```bash
git clone <repo>
cd TCC-CONSOLIDACION-INFORMES
cp .env.example .env && nano .env
docker-compose up -d
docker-compose --profile migrate up migrate
```

### Scheduler multi-instancia

Si se escala a mÃ¡s de 1 rÃ©plica del backend, usar APScheduler con JobStore en PostgreSQL
para evitar ejecuciones duplicadas:

```python
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
jobstores = {"default": SQLAlchemyJobStore(url=settings.database_url_sync)}
```

---

## Logs y eventos clave

| Evento                    | QuÃ© indica                                |
|---------------------------|-------------------------------------------|
| `job_daily_cycle_start`   | El job arrancÃ³                            |
| `job_daily_cycle_done`    | Ciclo completo (ver `email_sent`)         |
| `tracking_run_done`       | Resultado de consultas a TCC             |
| `email_sent`              | Correo enviado OK                         |
| `email_send_failed_final` | FallÃ³ tras todos los reintentos           |
| `alert_no_movement`       | Nueva alerta 72h detectada               |
| `job_weekly_report_done`  | Consolidado semanal generado              |

---

## PreparaciÃ³n para GitHub

```bash
echo ".env" >> .gitignore
echo "reports/" >> .gitignore
echo "__pycache__/" >> .gitignore
echo ".venv/" >> .gitignore

git init
git add .
git commit -m "feat: capa operativa â€” scheduler, reportes Excel/PDF, alertas, Docker"
git remote add origin https://github.com/tu-org/tcc-consolidacion.git
git push -u origin main
```

**Importante:** Solo subir `.env.example`, nunca `.env`.

---

Sistema interno ASTECO Â· Contacto: martinechavarriaurrea@gmail.com


