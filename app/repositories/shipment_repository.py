from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.shipment import Shipment
from app.repositories.base import BaseRepository
from app.utils.date_utils import utcnow


class ShipmentRepository(BaseRepository[Shipment]):
    model = Shipment

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_tracking_number(self, tracking_number: str) -> Shipment | None:
        result = await self.session.execute(
            select(Shipment).where(Shipment.tracking_number == tracking_number.upper())
        )
        return result.scalar_one_or_none()

    async def get_with_events(self, shipment_id: int) -> Shipment | None:
        result = await self.session.execute(
            select(Shipment)
            .options(selectinload(Shipment.tracking_events))
            .where(Shipment.id == shipment_id)
        )
        return result.scalar_one_or_none()

    async def get_active(self) -> list[Shipment]:
        result = await self.session.execute(
            select(Shipment).where(Shipment.is_active == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def get_paginated(
        self,
        page: int = 1,
        page_size: int = 50,
        is_active: bool | None = None,
        current_status: str | None = None,
        advisor_name: str | None = None,
    ) -> tuple[list[Shipment], int]:
        stmt = select(Shipment)
        count_stmt = select(func.count()).select_from(Shipment)

        if is_active is not None:
            stmt = stmt.where(Shipment.is_active == is_active)
            count_stmt = count_stmt.where(Shipment.is_active == is_active)
        if current_status:
            stmt = stmt.where(Shipment.current_status == current_status)
            count_stmt = count_stmt.where(Shipment.current_status == current_status)
        if advisor_name:
            stmt = stmt.where(Shipment.advisor_name.ilike(f"%{advisor_name}%"))
            count_stmt = count_stmt.where(Shipment.advisor_name.ilike(f"%{advisor_name}%"))

        total = (await self.session.execute(count_stmt)).scalar_one()
        stmt = stmt.offset((page - 1) * page_size).limit(page_size).order_by(Shipment.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def mark_delivered(self, shipment: Shipment, delivered_at: datetime) -> Shipment:
        shipment.delivered_at = delivered_at
        shipment.is_active = False
        shipment.updated_at = utcnow()
        await self.session.flush()
        return shipment

    async def close(self, shipment: Shipment) -> Shipment:
        shipment.closed_at = utcnow()
        shipment.is_active = False
        shipment.updated_at = utcnow()
        await self.session.flush()
        return shipment

    async def count_by_status(self) -> list[tuple[str, int]]:
        result = await self.session.execute(
            select(Shipment.current_status, func.count().label("cnt"))
            .where(Shipment.is_active == True)  # noqa: E712
            .group_by(Shipment.current_status)
        )
        return list(result.all())

    async def count_by_advisor(self) -> list[tuple[str, int, int, int]]:
        """(advisor_name, total, active, delivered)"""
        result = await self.session.execute(
            select(
                Shipment.advisor_name,
                func.count().label("total"),
                func.sum(Shipment.is_active).label("active"),
                func.count(Shipment.delivered_at).label("delivered"),
            ).group_by(Shipment.advisor_name)
        )
        return list(result.all())
