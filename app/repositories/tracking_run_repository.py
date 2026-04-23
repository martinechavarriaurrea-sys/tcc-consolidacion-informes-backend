from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_run import TrackingRun
from app.repositories.base import BaseRepository


class TrackingRunRepository(BaseRepository[TrackingRun]):
    model = TrackingRun

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_latest(self) -> TrackingRun | None:
        result = await self.session.execute(
            select(TrackingRun).order_by(TrackingRun.started_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def get_recent(self, limit: int = 20) -> list[TrackingRun]:
        result = await self.session.execute(
            select(TrackingRun).order_by(TrackingRun.started_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
