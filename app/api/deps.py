from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.shipment_service import ShipmentService
from app.services.tracking_service import TrackingService
from app.services.report_service import ReportService
from app.services.alert_service import AlertService


async def get_shipment_service(
    session: AsyncSession = Depends(get_db),
) -> ShipmentService:
    return ShipmentService(session)


async def get_tracking_service(
    session: AsyncSession = Depends(get_db),
) -> TrackingService:
    return TrackingService(session)


async def get_report_service(
    session: AsyncSession = Depends(get_db),
) -> ReportService:
    return ReportService(session)


async def get_alert_service(
    session: AsyncSession = Depends(get_db),
) -> AlertService:
    return AlertService(session)
