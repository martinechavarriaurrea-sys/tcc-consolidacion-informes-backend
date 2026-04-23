from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DuplicateError, NotFoundError
from app.core.logging import get_logger
from app.models.shipment import Shipment
from app.repositories.shipment_repository import ShipmentRepository
from app.schemas.shipment import ShipmentCreate, ShipmentUpdate
from app.utils.date_utils import utcnow

logger = get_logger(__name__)


class ShipmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = ShipmentRepository(session)

    async def create(self, data: ShipmentCreate) -> Shipment:
        existing = await self.repo.get_by_tracking_number(data.tracking_number)
        if existing:
            raise DuplicateError("Shipment", "tracking_number", data.tracking_number)

        shipment = Shipment(
            tracking_number=data.tracking_number,
            advisor_name=data.advisor_name,
            client_name=data.client_name,
            package_type=data.package_type,
            destination=data.destination,
            current_status="registrado",
            first_seen_at=utcnow(),
            is_active=True,
        )
        result = await self.repo.add(shipment)
        logger.info("shipment_created", tracking=result.tracking_number, advisor=result.advisor_name)
        return result

    async def get_or_raise(self, shipment_id: int) -> Shipment:
        shipment = await self.repo.get(shipment_id)
        if not shipment:
            raise NotFoundError("Shipment", shipment_id)
        return shipment

    async def get_by_tracking_or_raise(self, tracking_number: str) -> Shipment:
        shipment = await self.repo.get_by_tracking_number(tracking_number)
        if not shipment:
            raise NotFoundError("Shipment", tracking_number)
        return shipment

    async def get_detail(self, shipment_id: int) -> Shipment:
        shipment = await self.repo.get_with_events(shipment_id)
        if not shipment:
            raise NotFoundError("Shipment", shipment_id)
        return shipment

    async def list(
        self,
        page: int = 1,
        page_size: int = 50,
        is_active: bool | None = None,
        current_status: str | None = None,
        advisor_name: str | None = None,
    ) -> tuple[list[Shipment], int]:
        return await self.repo.get_paginated(page, page_size, is_active, current_status, advisor_name)

    async def update(self, shipment_id: int, data: ShipmentUpdate) -> Shipment:
        shipment = await self.get_or_raise(shipment_id)
        changes = data.model_dump(exclude_none=True)
        for field, value in changes.items():
            setattr(shipment, field, value)
        shipment.updated_at = utcnow()
        return shipment

    async def close(self, shipment_id: int) -> Shipment:
        shipment = await self.get_or_raise(shipment_id)
        return await self.repo.close(shipment)
