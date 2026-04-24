from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.core.config import get_settings
from app.core.logging import get_logger
from app.jobs.tracking_job import job_check_alerts, job_daily_cycle, job_weekly_report

router = APIRouter(prefix="/cron", tags=["cron"])
settings = get_settings()
logger = get_logger(__name__)

BOGOTA_TZ = ZoneInfo("America/Bogota")
CycleLabel = Literal["0700", "1200", "1600"]


def _verify_cron_authorization(authorization: str | None) -> None:
    if settings.cron_secret and authorization != f"Bearer {settings.cron_secret}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized cron request.",
        )


def _cycle_for_bogota_time(now: datetime) -> CycleLabel | None:
    if now.minute != 0:
        return None
    return {7: "0700", 12: "1200", 16: "1600"}.get(now.hour)


@router.get("/daily-dispatch")
async def daily_dispatch(
    cycle: CycleLabel | None = Query(default=None),
    authorization: str | None = Header(default=None),
):
    """
    Vercel Cron entrypoint.

    The schedule is defined in UTC in vercel.json:
    12:00, 17:00, 21:00 UTC = 07:00, 12:00, 16:00 America/Bogota.
    On Mondays at 07:00 Bogota it also runs the weekly report.
    """
    _verify_cron_authorization(authorization)

    now = datetime.now(BOGOTA_TZ)
    cycle_label = cycle or _cycle_for_bogota_time(now)
    if cycle_label is None:
        logger.info("cron_daily_dispatch_skipped", bogota_time=now.isoformat())
        return {"status": "skipped", "reason": "outside_schedule", "bogota_time": now.isoformat()}

    ran = [f"daily_{cycle_label}"]
    logger.info("cron_daily_dispatch_start", cycle=cycle_label, bogota_time=now.isoformat())
    await job_daily_cycle(cycle_label)

    if now.weekday() == 0 and cycle_label == "0700":
        ran.append("weekly")
        await job_weekly_report()

    logger.info("cron_daily_dispatch_done", jobs=ran)
    return {"status": "completed", "jobs": ran, "bogota_time": now.isoformat()}


@router.get("/alerts")
async def alerts_dispatch(authorization: str | None = Header(default=None)):
    _verify_cron_authorization(authorization)
    logger.info("cron_alerts_dispatch_start")
    await job_check_alerts()
    logger.info("cron_alerts_dispatch_done")
    return {"status": "completed", "jobs": ["alerts"]}
