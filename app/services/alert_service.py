"""
Detecta alertas de negocio sobre guías activas y persiste AlertEvents.

Tipos de alerta:
  no_movement_72h  — sin movimiento > 72h (configurable)
  novedad_tcc      — TCC reportó novedad con mensaje específico
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
ALERT_NOVEDAD = "novedad_tcc"


class AlertService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipment_repo = ShipmentRepository(session)
        self.event_repo = TrackingEventRepository(session)
        self.alert_repo = AlertEventRepository(session)

    async def check_all(self) -> list[AlertEvent]:
        """Evalúa todas las guías activas y genera/resuelve alertas.
        También resuelve alertas abiertas de guías ya inactivas."""
        from sqlalchemy import select as _select
        from app.models.shipment import Shipment as _Shipment
        from app.models.alert_event import AlertEvent as _AlertEvent

        # Resolver alertas de guías inactivas que quedaron abiertas
        inactive_with_alerts = await self.session.execute(
            _select(_Shipment.id).where(
                _Shipment.is_active == False,  # noqa: E712
                _Shipment.id.in_(
                    _select(_AlertEvent.shipment_id).where(_AlertEvent.resolved_at.is_(None)).distinct()
                )
            )
        )
        for (sid,) in inactive_with_alerts.all():
            await self.resolve_all_for_shipment(sid)

        active = await self.shipment_repo.get_active()
        new_alerts: list[AlertEvent] = []

        for shipment in active:
            alert = await self._check_no_movement(shipment)
            if alert:
                new_alerts.append(alert)
            await self._resolve_stale_no_movement(shipment)

            novedad_alert = await self._check_novedad(shipment)
            if novedad_alert:
                new_alerts.append(novedad_alert)
            await self._resolve_stale_novedad(shipment)

        logger.info("alert_check_done", new=len(new_alerts), evaluated=len(active))
        return new_alerts

    async def resolve_all_for_shipment(self, shipment_id: int) -> None:
        """Resuelve todas las alertas abiertas de una guía (al cerrar/entregar/reemplazar)."""
        for alert_type in (ALERT_NO_MOVEMENT, ALERT_NOVEDAD):
            await self.alert_repo.resolve_open(shipment_id, alert_type)

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
        """Si la guía tuvo movimiento reciente, cierra la alerta de sin movimiento."""
        reference_dt = shipment.current_status_at or shipment.first_seen_at
        threshold_hours = settings.alert_no_movement_hours
        if not is_older_than_hours(reference_dt, threshold_hours):
            await self.alert_repo.resolve_open(shipment.id, ALERT_NO_MOVEMENT)

    async def _check_novedad(self, shipment: Shipment) -> AlertEvent | None:
        """Crea alerta de novedad con el mensaje TCC cuando el estado es novedad."""
        if shipment.current_status != NormalizedStatus.NOVEDAD:
            return None

        already_open = await self.alert_repo.has_open_alert(shipment.id, ALERT_NOVEDAD)
        if already_open:
            return None

        msg = shipment.current_status_raw or "Novedad reportada por TCC"
        logger.warning("alert_novedad_tcc", tracking=shipment.tracking_number, msg=msg[:80])

        alert = AlertEvent(
            shipment_id=shipment.id,
            alert_type=ALERT_NOVEDAD,
            triggered_at=utcnow(),
            details={
                "tracking_number": shipment.tracking_number,
                "advisor_name": shipment.advisor_name,
                "cliente": shipment.client_name,
                "mensaje_tcc": msg,
            },
        )
        return await self.alert_repo.add(alert)

    async def _resolve_stale_novedad(self, shipment: Shipment) -> None:
        """Si el estado ya no es novedad, resuelve la alerta de novedad."""
        if shipment.current_status != NormalizedStatus.NOVEDAD:
            await self.alert_repo.resolve_open(shipment.id, ALERT_NOVEDAD)

    async def get_shipments_without_movement(self) -> list[Shipment]:
        """Retorna guías activas que llevan más de N horas sin movimiento."""
        active = await self.shipment_repo.get_active()
        threshold = settings.alert_no_movement_hours
        return [
            s for s in active
            if is_older_than_hours(s.current_status_at or s.first_seen_at, threshold)
        ]
