from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_event import ShipmentTrackingEvent
from app.repositories.base import BaseRepository


class TrackingEventRepository(BaseRepository[ShipmentTrackingEvent]):
    model = ShipmentTrackingEvent

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_shipment(self, shipment_id: int) -> list[ShipmentTrackingEvent]:
        result = await self.session.execute(
            select(ShipmentTrackingEvent)
            .where(ShipmentTrackingEvent.shipment_id == shipment_id)
            .order_by(ShipmentTrackingEvent.event_at.desc().nullslast())
        )
        return list(result.scalars().all())

    async def get_latest(self, shipment_id: int) -> ShipmentTrackingEvent | None:
        result = await self.session.execute(
            select(ShipmentTrackingEvent)
            .where(ShipmentTrackingEvent.shipment_id == shipment_id)
            .order_by(ShipmentTrackingEvent.event_at.desc().nullslast())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def status_exists(self, shipment_id: int, status_raw: str) -> bool:
        """Evita insertar eventos duplicados con el mismo estado raw."""
        result = await self.session.execute(
            select(ShipmentTrackingEvent.id)
            .where(
                ShipmentTrackingEvent.shipment_id == shipment_id,
                ShipmentTrackingEvent.status_raw == status_raw,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
