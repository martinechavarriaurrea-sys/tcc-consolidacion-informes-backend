from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, status
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.logging import get_logger
from app.jobs.tracking_job import (
    job_check_alerts,
    job_cleanup_old_guias,
    job_daily_cycle,
    job_weekly_report,
)

router = APIRouter(prefix="/cron", tags=["cron"])
settings = get_settings()
logger = get_logger(__name__)

BOGOTA_TZ = ZoneInfo("America/Bogota")
CycleLabel = Literal["0700", "1200", "1600"]
GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_JWKS_URL = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"
ALLOWED_GITHUB_EVENTS = {"schedule", "workflow_dispatch"}


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None

    return token.strip()


async def _verify_github_oidc_token(token: str) -> bool:
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            logger.warning("cron_github_oidc_missing_kid")
            return False

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(GITHUB_OIDC_JWKS_URL)
            response.raise_for_status()
            jwks = response.json()

        key = next((item for item in jwks.get("keys", []) if item.get("kid") == kid), None)
        if key is None:
            logger.warning("cron_github_oidc_unknown_kid", kid=kid)
            return False

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.github_oidc_audience,
            issuer=GITHUB_OIDC_ISSUER,
        )

    except (JWTError, httpx.HTTPError, ValueError) as exc:
        logger.warning("cron_github_oidc_invalid", error=str(exc))
        return False

    repository = claims.get("repository")
    ref = claims.get("ref")
    event_name = claims.get("event_name")
    if repository != settings.github_oidc_repository:
        logger.warning("cron_github_oidc_repository_rejected", repository=repository)
        return False
    if ref != settings.github_oidc_ref:
        logger.warning("cron_github_oidc_ref_rejected", ref=ref)
        return False
    if event_name not in ALLOWED_GITHUB_EVENTS:
        logger.warning("cron_github_oidc_event_rejected", event_name=event_name)
        return False

    return True


async def _verify_cron_authorization(authorization: str | None) -> None:
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized cron request.",
        )

    if settings.cron_secret and token == settings.cron_secret:
        return

    if await _verify_github_oidc_token(token):
        return

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
    GitHub Actions/Vercel Cron entrypoint.

    The schedule is defined in UTC:
    12:00, 17:00, 21:00 UTC = 07:00, 12:00, 16:00 America/Bogota.
    On Mondays at 07:00 Bogota it also runs the weekly report.
    """
    await _verify_cron_authorization(authorization)

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
    await _verify_cron_authorization(authorization)
    logger.info("cron_alerts_dispatch_start")
    await job_check_alerts()
    logger.info("cron_alerts_dispatch_done")
    return {"status": "completed", "jobs": ["alerts"]}


@router.get("/weekly")
async def weekly_dispatch(authorization: str | None = Header(default=None)):
    await _verify_cron_authorization(authorization)
    logger.info("cron_weekly_dispatch_start")
    await job_weekly_report()
    logger.info("cron_weekly_dispatch_done")
    return {"status": "completed", "jobs": ["weekly"]}


@router.get("/cleanup")
async def cleanup_dispatch(authorization: str | None = Header(default=None)):
    await _verify_cron_authorization(authorization)
    logger.info("cron_cleanup_dispatch_start")
    await job_cleanup_old_guias()
    logger.info("cron_cleanup_dispatch_done")
    return {"status": "completed", "jobs": ["cleanup"]}
