"""
Scheduler APScheduler — horarios fijos de negocio (America/Bogota).

HORARIOS PROGRAMADOS
─────────────────────────────────────────────────────────────────────────────
Job                   │ Trigger               │ Descripción
──────────────────────┼───────────────────────┼───────────────────────────────
daily_cycle_0700      │ Cron 07:00 lun–dom    │ Consulta TCC + reporte + email
daily_cycle_1200      │ Cron 12:00 lun–dom    │ Consulta TCC + reporte + email
daily_cycle_1600      │ Cron 16:00 lun–dom    │ Consulta TCC + reporte + email
weekly_report         │ Cron lunes 07:00      │ Consolidado semana anterior
alert_check           │ Intervalo 30 min      │ Detección alertas 72h + email
─────────────────────────────────────────────────────────────────────────────

CONTROL DE SOLAPAMIENTO
max_instances=1 en cada job: si una ejecución anterior no terminó cuando
la siguiente dispara, la segunda se descarta (misfire) en vez de superponerse.
misfire_grace_time=300s: si el scheduler arranca tarde, acepta disparos
con hasta 5 min de retraso (común en reinicios de contenedor).

EJECUCIÓN MANUAL
Ver scripts/run_job.py o llamar directamente las funciones de tracking_job.py.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.core.logging import get_logger
from app.jobs.tracking_job import (
    job_check_alerts,
    job_daily_cycle,
    job_weekly_report,
    job_cleanup_old_guias,
)

logger = get_logger(__name__)
settings = get_settings()

_scheduler: AsyncIOScheduler | None = None

TZ = "America/Bogota"


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=TZ)
    return _scheduler


def setup_jobs(scheduler: AsyncIOScheduler) -> None:
    # ── Ciclos diarios ────────────────────────────────────────────────────────
    for hour, label in [(7, "0700"), (12, "1200"), (16, "1600")]:
        scheduler.add_job(
            job_daily_cycle,
            trigger=CronTrigger(hour=hour, minute=0, timezone=TZ),
            id=f"daily_cycle_{label}",
            name=f"Ciclo diario {label} — consulta + reporte + email",
            args=[label],
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )

    # ── Consolidado semanal: lunes 07:00 ─────────────────────────────────────
    scheduler.add_job(
        job_weekly_report,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=TZ),
        id="weekly_report",
        name="Consolidado semanal — lunes 07:00",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # ── Verificación de alertas 72h ───────────────────────────────────────────
    scheduler.add_job(
        job_check_alerts,
        trigger=IntervalTrigger(minutes=settings.alert_check_interval_minutes, timezone=TZ),
        id="alert_check",
        name=f"Verificación alertas 72h (cada {settings.alert_check_interval_minutes} min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )

    # ── Limpieza semanal: elimina guías entregadas hace más de 14 días ───────────
    scheduler.add_job(
        job_cleanup_old_guias,
        trigger=CronTrigger(day_of_week="mon", hour=6, minute=0, timezone=TZ),
        id="cleanup_old_guias",
        name="Limpieza guías > 14 días entregadas",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    jobs = scheduler.get_jobs()
    logger.info("scheduler_jobs_registered", count=len(jobs), jobs=[j.id for j in jobs])


async def start_scheduler() -> None:
    scheduler = get_scheduler()
    setup_jobs(scheduler)
    scheduler.start()
    logger.info("scheduler_started", timezone=TZ)


async def stop_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
