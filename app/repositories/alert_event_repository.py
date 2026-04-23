from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_event import AlertEvent
from app.repositories.base import BaseRepository
from app.utils.date_utils import utcnow


class AlertEventRepository(BaseRepository[AlertEvent]):
    model = AlertEvent

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def has_open_alert(self, shipment_id: int, alert_type: str) -> bool:
        result = await self.session.execute(
            select(AlertEvent.id)
            .where(
                AlertEvent.shipment_id == shipment_id,
                AlertEvent.alert_type == alert_type,
                AlertEvent.resolved_at == None,  # noqa: E711
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def resolve_open(self, shipment_id: int, alert_type: str) -> None:
        result = await self.session.execute(
            select(AlertEvent).where(
                AlertEvent.shipment_id == shipment_id,
                AlertEvent.alert_type == alert_type,
                AlertEvent.resolved_at == None,  # noqa: E711
            )
        )
        for alert in result.scalars().all():
            alert.resolved_at = utcnow()
        await self.session.flush()
