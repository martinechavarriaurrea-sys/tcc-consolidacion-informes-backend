"""
Ejecutor manual de jobs — para pruebas, debugging y ejecuciones de emergencia.

Uso:
    python scripts/run_job.py daily 0700
    python scripts/run_job.py daily 1200
    python scripts/run_job.py daily 1600
    python scripts/run_job.py weekly
    python scripts/run_job.py alerts

Requiere:
    - .env configurado
    - Base de datos accesible
    - pip install -r requirements.txt

El script corre el job seleccionado de forma síncrona y registra en BD.
Ideal para:
- Forzar un ciclo fuera de horario
- Probar generación de archivos antes del primer día real
- Recuperar un ciclo fallido
"""

import asyncio
import sys
import os

# Asegura que el import encuentre el paquete app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger("run_job")

VALID_JOBS = {
    "daily": ["0700", "1200", "1600"],
    "weekly": None,
    "alerts": None,
}

USAGE = """
Uso: python scripts/run_job.py <job> [cycle_label]

Jobs disponibles:
  daily 0700   — Ciclo diario 07:00 (consulta + reporte + email)
  daily 1200   — Ciclo diario 12:00
  daily 1600   — Ciclo diario 16:00
  weekly       — Consolidado semanal (semana anterior)
  alerts       — Verificación de alertas 72h

Ejemplos:
  python scripts/run_job.py daily 0700
  python scripts/run_job.py weekly
  python scripts/run_job.py alerts
"""


async def main(job_name: str, cycle_label: str | None) -> None:
    from app.jobs.tracking_job import job_check_alerts, job_daily_cycle, job_weekly_report

    logger.info("manual_job_start", job=job_name, cycle=cycle_label)

    if job_name == "daily":
        if cycle_label not in VALID_JOBS["daily"]:
            print(f"ERROR: cycle_label debe ser uno de: {VALID_JOBS['daily']}")
            sys.exit(1)
        await job_daily_cycle(cycle_label)

    elif job_name == "weekly":
        await job_weekly_report()

    elif job_name == "alerts":
        await job_check_alerts()

    logger.info("manual_job_done", job=job_name)
    print(f"\n✓ Job '{job_name}' completado. Revisa los logs y los archivos en ./reports/")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    job = sys.argv[1].lower()
    cycle = sys.argv[2] if len(sys.argv) > 2 else None

    if job not in VALID_JOBS:
        print(f"ERROR: Job desconocido '{job}'\n{USAGE}")
        sys.exit(1)

    if job == "daily" and cycle is None:
        print("ERROR: El job 'daily' requiere un cycle_label: 0700 | 1200 | 1600\n")
        sys.exit(1)

    asyncio.run(main(job, cycle))
