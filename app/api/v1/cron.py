import base64
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Header, HTTPException, Query, status
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.integrations.tcc.base import TrackingEventData, TrackingResult
from app.jobs.tracking_job import (
    job_check_alerts,
    job_check_alerts_data,
    job_cleanup_old_guias,
    job_daily_cycle,
    job_daily_report_only,
    job_weekly_report,
    job_weekly_report_pdf,
)
from app.models.tracking_run import TrackingRun
from app.services.tracking_service import TrackingService
from app.utils.date_utils import utcnow

router = APIRouter(prefix="/cron", tags=["cron"])
settings = get_settings()
logger = get_logger(__name__)

BOGOTA_TZ = ZoneInfo("America/Bogota")
CycleLabel = Literal["0700", "1200", "1600"]
GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_JWKS_URL = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"
ALLOWED_GITHUB_EVENTS = {"schedule", "workflow_dispatch"}


class CronTrackingEventPayload(BaseModel):
    status_raw: str
    status_normalized: str | None = None
    event_at: datetime | None = None
    observed_at: datetime | None = None
    notes: str | None = None
    payload_snapshot: dict[str, Any] | None = None

    def to_event_data(self) -> TrackingEventData:
        return TrackingEventData(
            status_raw=self.status_raw,
            status_normalized=self.status_normalized or "",
            event_at=self.event_at,
            observed_at=self.observed_at or utcnow(),
            notes=self.notes,
            payload_snapshot=self.payload_snapshot,
        )


class CronTrackingResultPayload(BaseModel):
    tracking_number: str
    current_status_raw: str | None = None
    current_status_normalized: str | None = None
    current_status_at: datetime | None = None
    destination: str | None = None
    package_type: str | None = None
    client_name: str | None = None
    events: list[CronTrackingEventPayload] = Field(default_factory=list)
    payload_snapshot: dict[str, Any] = Field(default_factory=dict)
    fetch_success: bool = False
    fetch_error: str | None = None
    provider: str = "github-actions"

    def to_tracking_result(self) -> TrackingResult:
        return TrackingResult(
            tracking_number=self.tracking_number,
            current_status_raw=self.current_status_raw,
            current_status_normalized=self.current_status_normalized,
            current_status_at=self.current_status_at,
            destination=self.destination,
            package_type=self.package_type,
            client_name=self.client_name,
            events=[event.to_event_data() for event in self.events],
            payload_snapshot=self.payload_snapshot,
            fetch_success=self.fetch_success,
            fetch_error=self.fetch_error,
            provider=self.provider,
        )


class CronTrackingIngestPayload(BaseModel):
    run_type: str = "github_actions"
    cycle_label: CycleLabel | None = None
    results: list[CronTrackingResultPayload]


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


@router.post("/ingest-tracking")
async def ingest_tracking_results(
    payload: CronTrackingIngestPayload,
    authorization: str | None = Header(default=None),
):
    await _verify_cron_authorization(authorization)

    checked = updated = failed = 0
    errors: list[str] = []

    async with AsyncSessionLocal() as session:
        tracking_svc = TrackingService(session)
        run = TrackingRun(run_type=payload.run_type, started_at=utcnow(), status="running")
        run = await tracking_svc.run_repo.add(run)

        for item in payload.results:
            checked += 1
            shipment = await tracking_svc.shipment_repo.get_by_tracking_number(item.tracking_number)
            if shipment is None:
                failed += 1
                errors.append(f"{item.tracking_number}: not registered")
                continue

            success, was_updated = await tracking_svc.apply_result(shipment, item.to_tracking_result())
            if not success:
                failed += 1
                errors.append(item.tracking_number)
            elif was_updated:
                updated += 1

        run.finished_at = utcnow()
        run.shipments_checked = checked
        run.shipments_updated = updated
        run.shipments_failed = failed
        run.status = "completed" if failed == 0 else "partial" if updated > 0 else "failed"
        run.error_summary = "; ".join(errors[:20]) if errors else None
        run_started_at = run.started_at
        await session.commit()

    jobs = ["tracking_ingest"]
    pdf_b64: str | None = None
    pdf_filename: str | None = None
    weekly_pdf_b64: str | None = None
    weekly_pdf_filename: str | None = None
    weekly_period: str | None = None

    if payload.cycle_label:
        pdf_path = await job_daily_report_only(payload.cycle_label, run_started_at)
        jobs.append(f"daily_report_{payload.cycle_label}")
        if pdf_path and pdf_path.exists():
            pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode()
            pdf_filename = pdf_path.name

        now = datetime.now(BOGOTA_TZ)
        if now.weekday() == 0 and payload.cycle_label == "0700":
            result = await job_weekly_report_pdf()
            if result:
                w_path, w_start, w_end = result
                weekly_pdf_b64 = base64.b64encode(w_path.read_bytes()).decode()
                weekly_pdf_filename = w_path.name
                weekly_period = f"{w_start} al {w_end}"
            jobs.append("weekly")

    logger.info("cron_ingest_tracking_done", checked=checked, updated=updated,
                failed=failed, has_pdf=pdf_b64 is not None)
    return {
        "status": "completed",
        "jobs": jobs,
        "checked": checked,
        "updated": updated,
        "failed": failed,
        "pdf_b64": pdf_b64,
        "pdf_filename": pdf_filename,
        "weekly_pdf_b64": weekly_pdf_b64,
        "weekly_pdf_filename": weekly_pdf_filename,
        "weekly_period": weekly_period,
    }


@router.get("/alerts")
async def alerts_dispatch(authorization: str | None = Header(default=None)):
    await _verify_cron_authorization(authorization)
    logger.info("cron_alerts_dispatch_start")
    await job_check_alerts()
    logger.info("cron_alerts_dispatch_done")
    return {"status": "completed", "jobs": ["alerts"]}


@router.get("/alert-data")
async def alert_data_dispatch(authorization: str | None = Header(default=None)):
    """Detecta alertas 72h, las registra en BD y retorna datos para que GitHub Actions envie email."""
    await _verify_cron_authorization(authorization)
    logger.info("cron_alert_data_start")
    alerts = await job_check_alerts_data()
    logger.info("cron_alert_data_done", count=len(alerts))
    return {"status": "completed", "new_alerts": alerts, "count": len(alerts)}


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
