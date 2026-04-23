from fastapi import APIRouter, Depends, status

from app.api.deps import get_shipment_service, get_tracking_service
from app.repositories.tracking_event_repository import TrackingEventRepository
from app.schemas.tracking import ManualRunRequest, TrackingRunOut
from app.services.shipment_service import ShipmentService
from app.services.tracking_service import TrackingService
from app.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends as FastDepends

router = APIRouter(prefix="/tracking", tags=["Tracking"])


@router.post(
    "/run-manual",
    response_model=TrackingRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_manual_tracking(
    payload: ManualRunRequest,
    svc: TrackingService = Depends(get_tracking_service),
):
    """Dispara una corrida de tracking manual. Si no se especifican guías, procesa todas las activas."""
    run = await svc.run_full(run_type="manual", tracking_numbers=payload.tracking_numbers)
    return run


@router.get("/history/{tracking_number}")
async def get_tracking_history(
    tracking_number: str,
    shipment_svc: ShipmentService = Depends(get_shipment_service),
    session: AsyncSession = FastDepends(get_db),
):
    """Historial completo de eventos de una guía."""
    shipment = await shipment_svc.get_by_tracking_or_raise(tracking_number)
    event_repo = TrackingEventRepository(session)
    events = await event_repo.get_by_shipment(shipment.id)
    from app.schemas.shipment import TrackingEventOut
    return {
        "tracking_number": tracking_number,
        "current_status": shipment.current_status,
        "events": [TrackingEventOut.model_validate(e) for e in events],
    }
