"""
Detecta alertas de negocio sobre guías activas y persiste AlertEvents.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.alert_event import AlertEvent
from app.models.shipment import Shipment
from app.repositories.alert_event_repository import AlertEventRepository
from app.repositories.shipment_repository import ShipmentRepository
from app.repositories.tracking_event_repository import TrackingEventRepository
from app.utils.date_utils import is_older_than_hours, utcnow
from app.utils.status_normalizer import ISSUE_STATUSES, NormalizedStatus

logger = get_logger(__name__)
settings = get_settings()

ALERT_NO_MOVEMENT = "no_movement_72h"


class AlertService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipment_repo = ShipmentRepository(session)
        self.event_repo = TrackingEventRepository(session)
        self.alert_repo = AlertEventRepository(session)

    async def check_all(self) -> list[AlertEvent]:
        """Evalúa todas las guías activas y genera alertas donde aplique."""
        active = await self.shipment_repo.get_active()
        new_alerts: list[AlertEvent] = []

        for shipment in active:
            alert = await self._check_no_movement(shipment)
            if alert:
                new_alerts.append(alert)
            await self._resolve_stale_no_movement(shipment)

        logger.info("alert_check_done", new=len(new_alerts), evaluated=len(active))
        return new_alerts

    async def _check_no_movement(self, shipment: Shipment) -> AlertEvent | None:
        threshold_hours = settings.alert_no_movement_hours
        reference_dt = shipment.current_status_at or shipment.first_seen_at

        if not is_older_than_hours(reference_dt, threshold_hours):
            return None

        already_open = await self.alert_repo.has_open_alert(shipment.id, ALERT_NO_MOVEMENT)
        if already_open:
            return None

        logger.warning(
            "alert_no_movement",
            tracking=shipment.tracking_number,
            advisor=shipment.advisor_name,
            hours=threshold_hours,
        )
        alert = AlertEvent(
            shipment_id=shipment.id,
            alert_type=ALERT_NO_MOVEMENT,
            triggered_at=utcnow(),
            details={
                "tracking_number": shipment.tracking_number,
                "advisor_name": shipment.advisor_name,
                "last_status": shipment.current_status,
                "last_movement_at": reference_dt.isoformat() if reference_dt else None,
                "hours_without_movement": threshold_hours,
            },
        )
        return await self.alert_repo.add(alert)

    async def _resolve_stale_no_movement(self, shipment: Shipment) -> None:
        """Si la guía tuvo movimiento, cierra la alerta abierta."""
        reference_dt = shipment.current_status_at or shipment.first_seen_at
        threshold_hours = settings.alert_no_movement_hours
        if not is_older_than_hours(reference_dt, threshold_hours):
            await self.alert_repo.resolve_open(shipment.id, ALERT_NO_MOVEMENT)

    async def get_shipments_without_movement(self) -> list[Shipment]:
        """Retorna guías activas que llevan más de N horas sin movimiento."""
        active = await self.shipment_repo.get_active()
        threshold = settings.alert_no_movement_hours
        return [
            s for s in active
            if is_older_than_hours(s.current_status_at or s.first_seen_at, threshold)
        ]
