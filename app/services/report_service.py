"""
Genera consolidados diarios y semanales.
Los datos quedan listos para ser enviados por email o exportados.
"""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.shipment import Shipment
from app.models.weekly_rollup import WeeklyRollup
from app.repositories.shipment_repository import ShipmentRepository
from app.schemas.dashboard import AdvisorBreakdown, DashboardSummary, StatusBreakdown
from app.services.alert_service import AlertService
from app.utils.date_utils import start_of_today, utcnow, week_boundaries
from app.utils.status_normalizer import ISSUE_STATUSES, NormalizedStatus

logger = get_logger(__name__)


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipment_repo = ShipmentRepository(session)

    async def get_dashboard_summary(self) -> DashboardSummary:
        from app.repositories.tracking_run_repository import TrackingRunRepository

        run_repo = TrackingRunRepository(self.session)
        last_run = await run_repo.get_latest()
        alert_svc = AlertService(self.session)
        no_movement = await alert_svc.get_shipments_without_movement()
        status_counts = await self.shipment_repo.count_by_status()
        advisor_rows = await self.shipment_repo.count_by_advisor()

        today_start = start_of_today()
        delivered_today_result = await self.session.execute(
            select(Shipment).where(
                Shipment.delivered_at >= today_start,
                Shipment.delivered_at.is_not(None),
            )
        )
        delivered_today = len(list(delivered_today_result.scalars().all()))

        issue_statuses_str = [s.value for s in ISSUE_STATUSES]
        with_issues_result = await self.session.execute(
            select(Shipment).where(
                Shipment.is_active == True,  # noqa: E712
                Shipment.current_status.in_(issue_statuses_str),
            )
        )
        total_issues = len(list(with_issues_result.scalars().all()))

        active_count_result = await self.session.execute(
            select(Shipment).where(Shipment.is_active == True)  # noqa: E712
        )
        total_active = len(list(active_count_result.scalars().all()))

        return DashboardSummary(
            total_active=total_active,
            total_delivered_today=delivered_today,
            total_with_issues=total_issues,
            total_no_movement_72h=len(no_movement),
            status_breakdown=[StatusBreakdown(status=s, count=c) for s, c in status_counts],
            advisor_breakdown=[
                AdvisorBreakdown(
                    advisor_name=row[0],
                    total=row[1] or 0,
                    active=int(row[2] or 0),
                    delivered=row[3] or 0,
                )
                for row in advisor_rows
            ],
            last_tracking_run=last_run.started_at if last_run else None,
            as_of=utcnow(),
        )

    async def generate_weekly_rollup(self, reference: date | None = None) -> WeeklyRollup:
        week_start, week_end = week_boundaries(reference)
        logger.info("generating_weekly_rollup", week_start=str(week_start), week_end=str(week_end))

        from sqlalchemy import and_, func
        from app.models.tracking_event import ShipmentTrackingEvent

        # Guías que tuvieron actividad en esta semana
        week_start_dt = datetime.combine(week_start, datetime.min.time())
        week_end_dt = datetime.combine(week_end, datetime.max.time())

        active_in_week = await self.session.execute(
            select(Shipment.id).where(
                Shipment.first_seen_at <= week_end_dt,
                Shipment.closed_at.is_(None) | (Shipment.closed_at >= week_start_dt),
            )
        )
        shipment_ids = [r[0] for r in active_in_week.all()]
        total = len(shipment_ids)

        delivered = await self.session.execute(
            select(func.count(Shipment.id)).where(
                Shipment.delivered_at >= week_start_dt,
                Shipment.delivered_at <= week_end_dt,
            )
        )
        total_delivered = delivered.scalar_one() or 0

        in_transit = await self.session.execute(
            select(func.count(Shipment.id)).where(
                Shipment.is_active == True,  # noqa: E712
                Shipment.current_status.in_([
                    NormalizedStatus.EN_TRANSITO,
                    NormalizedStatus.EN_RUTA,
                    NormalizedStatus.RECOGIDO,
                ]),
            )
        )
        total_in_transit = in_transit.scalar_one() or 0

        issue_statuses_str = [s.value for s in ISSUE_STATUSES]
        issues = await self.session.execute(
            select(func.count(Shipment.id)).where(
                Shipment.is_active == True,  # noqa: E712
                Shipment.current_status.in_(issue_statuses_str),
            )
        )
        total_issues = issues.scalar_one() or 0

        # Guías activas que vienen de semana anterior
        carried = await self.session.execute(
            select(func.count(Shipment.id)).where(
                Shipment.is_active == True,  # noqa: E712
                Shipment.first_seen_at < week_start_dt,
            )
        )
        total_carried = carried.scalar_one() or 0

        rollup = WeeklyRollup(
            week_start=week_start,
            week_end=week_end,
            generated_at=utcnow(),
            total_shipments=total,
            total_delivered=total_delivered,
            total_in_transit=total_in_transit,
            total_with_issues=total_issues,
            total_carried_forward=total_carried,
        )
        self.session.add(rollup)
        await self.session.flush()
        return rollup
