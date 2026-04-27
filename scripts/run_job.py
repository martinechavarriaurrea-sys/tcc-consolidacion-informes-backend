"""
Ejecutor manual de jobs para pruebas, recuperaciones y automatizacion externa.

Uso:
    python scripts/run_job.py daily 0700
    python scripts/run_job.py daily 1200
    python scripts/run_job.py daily 1600
    python scripts/run_job.py email 1600
    python scripts/run_job.py weekly
    python scripts/run_job.py alerts
    python scripts/run_job.py cleanup
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger("run_job")

VALID_JOBS = {
    "daily": ["0700", "1200", "1600"],
    "email": ["0700", "1200", "1600"],
    "weekly": None,
    "alerts": None,
    "cleanup": None,
}

USAGE = """
Uso: python scripts/run_job.py <job> [cycle_label]

Jobs disponibles:
  daily 0700   - Ciclo diario 07:00
  daily 1200   - Ciclo diario 12:00
  daily 1600   - Ciclo diario 16:00
  email 1600   - Envia reporte pendiente del ciclo por Outlook; si no existe, ejecuta ciclo completo
  weekly       - Consolidado semanal
  alerts       - Verificacion de alertas 72h
  cleanup      - Limpieza de guias antiguas
"""


async def main(job_name: str, cycle_label: str | None) -> None:
    from app.jobs.tracking_job import (
        job_check_alerts,
        job_cleanup_old_guias,
        job_daily_cycle,
        job_weekly_report,
    )

    logger.info("manual_job_start", job=job_name, cycle=cycle_label)

    if job_name == "daily":
        if cycle_label not in VALID_JOBS["daily"]:
            print(f"ERROR: cycle_label debe ser uno de: {VALID_JOBS['daily']}")
            sys.exit(1)
        await job_daily_cycle(cycle_label)
    elif job_name == "email":
        if cycle_label not in VALID_JOBS["email"]:
            print(f"ERROR: cycle_label debe ser uno de: {VALID_JOBS['email']}")
            sys.exit(1)
        from scripts.local_email_dispatcher import send_pending_daily

        await send_pending_daily(cycle_label, fallback_run=True)
    elif job_name == "weekly":
        await job_weekly_report()
    elif job_name == "alerts":
        await job_check_alerts()
    elif job_name == "cleanup":
        await job_cleanup_old_guias()

    logger.info("manual_job_done", job=job_name)
    print(f"\nJob '{job_name}' completado. Revisa logs y archivos en ./reports/")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(USAGE)
        sys.exit(1)

    job = sys.argv[1].lower()
    cycle = sys.argv[2] if len(sys.argv) > 2 else None

    if job not in VALID_JOBS:
        print(f"ERROR: Job desconocido '{job}'\n{USAGE}")
        sys.exit(1)

    if job in {"daily", "email"} and cycle is None:
        print(f"ERROR: El job '{job}' requiere un cycle_label: 0700 | 1200 | 1600\n")
        sys.exit(1)

    asyncio.run(main(job, cycle))
