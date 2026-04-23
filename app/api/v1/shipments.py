from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_shipment_service
from app.schemas.common import MessageResponse
from app.schemas.shipment import (
    ShipmentCreate,
    ShipmentDetailOut,
    ShipmentOut,
    ShipmentUpdate,
)
from app.services.shipment_service import ShipmentService

router = APIRouter(prefix="/shipments", tags=["Shipments"])


@router.post("", response_model=ShipmentOut, status_code=status.HTTP_201_CREATED)
async def create_shipment(
    payload: ShipmentCreate,
    svc: ShipmentService = Depends(get_shipment_service),
):
    """Registra una nueva guía para seguimiento."""
    return await svc.create(payload)


@router.get("", response_model=dict)
async def list_shipments(
    is_active: bool | None = Query(None),
    current_status: str | None = Query(None),
    advisor_name: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    svc: ShipmentService = Depends(get_shipment_service),
):
    """Lista guías con filtros y paginación."""
    items, total = await svc.list(page, page_size, is_active, current_status, advisor_name)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [ShipmentOut.model_validate(s) for s in items],
    }


@router.get("/{shipment_id}", response_model=ShipmentDetailOut)
async def get_shipment(
    shipment_id: int,
    svc: ShipmentService = Depends(get_shipment_service),
):
    """Detalle de una guía con historial de eventos."""
    return await svc.get_detail(shipment_id)


@router.patch("/{shipment_id}", response_model=ShipmentOut)
async def update_shipment(
    shipment_id: int,
    payload: ShipmentUpdate,
    svc: ShipmentService = Depends(get_shipment_service),
):
    """Actualiza datos editables de una guía (asesor, cliente, etc.)."""
    return await svc.update(shipment_id, payload)


@router.post("/{shipment_id}/close", response_model=MessageResponse)
async def close_shipment(
    shipment_id: int,
    svc: ShipmentService = Depends(get_shipment_service),
):
    """Cierra manualmente una guía (la saca del ciclo activo)."""
    await svc.close(shipment_id)
    return MessageResponse(message=f"Guía {shipment_id} cerrada exitosamente")
