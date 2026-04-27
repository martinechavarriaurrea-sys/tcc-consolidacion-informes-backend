from math import ceil
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.exceptions import DuplicateError, NotFoundError
from app.models.alert_event import AlertEvent
from app.models.shipment import Shipment
from app.models.tracking_event import ShipmentTrackingEvent
from app.schemas.shipment import ShipmentCreate
from app.services.shipment_service import ShipmentService
from app.services.tracking_service import TrackingService
from app.utils.date_utils import utcnow

router = APIRouter(prefix="/guias", tags=["guias"])

ALERT_TYPE_MAP = {
    "no_movement_72h": "sin_movimiento",
    "delivery_failed": "novedad",
    "returned": "novedad",
    "custom": "novedad",
}

ISSUE_STATUSES = {"novedad", "devuelto"}


def _dias_en_transito(shipment: Shipment) -> int | None:
    if shipment.shipping_date:
        end = shipment.delivered_at.date() if shipment.delivered_at else date.today()
        return max(0, (end - shipment.shipping_date).days)
    return None


def _to_resumen(shipment: Shipment, tiene_alerta: bool) -> dict:
    return {
        "id": str(shipment.id),
        "numero_guia": shipment.tracking_number,
        "asesor": shipment.advisor_name,
        "cliente": shipment.client_name,
        "estado_actual": shipment.current_status or "registrado",
        "fecha_ultima_actualizacion": shipment.updated_at.isoformat() if shipment.updated_at else None,
        "fecha_despacho": shipment.shipping_date.isoformat() if shipment.shipping_date else None,
        "dias_en_transito": _dias_en_transito(shipment),
        "activa": shipment.is_active,
        "tiene_alerta": tiene_alerta,
    }


def _to_detail(shipment: Shipment, alertas: list, historial: list) -> dict:
    ultima_novedad = None
    for ev in sorted(historial, key=lambda e: e.get("fecha", ""), reverse=True):
        if ev.get("estado", "").lower() in ("novedad", "devuelto"):
            ultima_novedad = ev.get("descripcion")
            break

    return {
        "id": str(shipment.id),
        "numero_guia": shipment.tracking_number,
        "asesor": shipment.advisor_name,
        "cliente": shipment.client_name,
        "estado_actual": shipment.current_status or "registrado",
        "estado_raw": shipment.current_status_raw,
        "fecha_creacion": shipment.first_seen_at.isoformat() if shipment.first_seen_at else None,
        "fecha_ultima_actualizacion": shipment.updated_at.isoformat() if shipment.updated_at else None,
        "dias_en_transito": _dias_en_transito(shipment),
        "activa": shipment.is_active,
        "alertas": alertas,
        "historial": historial,
        "ultima_novedad": ultima_novedad,
        "observacion": shipment.destination,
    }


class RegistrarGuiaPayload(BaseModel):
    numero_guia: str
    asesor: str
    cliente: str | None = None
    fecha_despacho: date | None = None


@router.get("")
async def list_guias(
    estado: str | None = Query(None),
    asesor: str | None = Query(None),
    search: str | None = Query(None),
    activa: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    # Mostrar: activas + entregadas en las últimas 2 semanas
    from datetime import datetime, timedelta, timezone
    dos_semanas = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)

    stmt = select(Shipment).where(
        (Shipment.is_active == True) |
        (Shipment.delivered_at >= dos_semanas) |
        (Shipment.closed_at >= dos_semanas)
    )
    count_stmt = select(func.count()).select_from(Shipment).where(
        (Shipment.is_active == True) |
        (Shipment.delivered_at >= dos_semanas) |
        (Shipment.closed_at >= dos_semanas)
    )

    if activa is not None:
        stmt = stmt.where(Shipment.is_active == activa)
        count_stmt = count_stmt.where(Shipment.is_active == activa)
    if estado:
        stmt = stmt.where(Shipment.current_status == estado)
        count_stmt = count_stmt.where(Shipment.current_status == estado)
    if asesor:
        stmt = stmt.where(Shipment.advisor_name.ilike(f"%{asesor}%"))
        count_stmt = count_stmt.where(Shipment.advisor_name.ilike(f"%{asesor}%"))
    if search:
        cond = or_(
            Shipment.tracking_number.ilike(f"%{search}%"),
            Shipment.client_name.ilike(f"%{search}%"),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = stmt.order_by(Shipment.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    shipments = list((await db.execute(stmt)).scalars().all())

    shipment_ids = [s.id for s in shipments]
    alert_map: dict[int, bool] = {}
    if shipment_ids:
        alert_rows = await db.execute(
            select(AlertEvent.shipment_id)
            .where(AlertEvent.shipment_id.in_(shipment_ids), AlertEvent.resolved_at.is_(None))
            .distinct()
        )
        alert_ids = {row[0] for row in alert_rows.all()}
        for sid in shipment_ids:
            alert_map[sid] = sid in alert_ids

    items = [_to_resumen(s, alert_map.get(s.id, False)) for s in shipments]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": ceil(total / page_size) if total else 1}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_guia(
    payload: RegistrarGuiaPayload,
    db: AsyncSession = Depends(get_db),
):
    svc = ShipmentService(db)
    try:
        shipment = await svc.create(ShipmentCreate(
            tracking_number=payload.numero_guia,
            advisor_name=payload.asesor,
            client_name=payload.cliente,
            shipping_date=payload.fecha_despacho,
        ))
    except DuplicateError:
        raise HTTPException(status_code=409, detail="La guía ya existe en el sistema.")

    import asyncio
    asyncio.create_task(_query_tcc_background(shipment.tracking_number))

    return _to_detail(shipment, [], [])


async def _query_tcc_background(tracking_number: str) -> None:
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        try:
            await TrackingService(session).run_full(
                run_type="manual", tracking_numbers=[tracking_number]
            )
        except Exception:
            pass


@router.get("/{guia_id}")
async def get_guia(guia_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = int(guia_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Guía no encontrada.")

    result = await db.execute(
        select(Shipment)
        .options(selectinload(Shipment.tracking_events), selectinload(Shipment.alert_events))
        .where(Shipment.id == sid)
    )
    shipment = result.scalar_one_or_none()
    if not shipment:
        raise HTTPException(status_code=404, detail="Guía no encontrada.")

    alertas = [
        {
            "id": str(a.id),
            "tipo": ALERT_TYPE_MAP.get(a.alert_type, "novedad"),
            "mensaje": (a.details or {}).get("message", a.alert_type),
            "fecha": a.triggered_at.isoformat() if a.triggered_at else None,
        }
        for a in (shipment.alert_events or [])
        if not a.resolved_at
    ]

    historial = sorted(
        [
            {
                "id": str(e.id),
                "fecha": (e.event_at or e.observed_at).isoformat() if (e.event_at or e.observed_at) else None,
                "estado": e.status_normalized or e.status_raw,
                "descripcion": e.notes or e.status_raw,
                "ubicacion": None,
            }
            for e in (shipment.tracking_events or [])
        ],
        key=lambda x: x["fecha"] or "",
    )

    return _to_detail(shipment, alertas, historial)


@router.patch("/{guia_id}/cerrar")
async def cerrar_guia(guia_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = int(guia_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Guía no encontrada.")
    svc = ShipmentService(db)
    try:
        shipment = await svc.close(sid)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Guía no encontrada.")
    return _to_detail(shipment, [], [])


@router.post("/{guia_id}/refresh")
async def refresh_guia(guia_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = int(guia_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Guía no encontrada.")

    result = await db.execute(select(Shipment).where(Shipment.id == sid))
    shipment = result.scalar_one_or_none()
    if not shipment:
        raise HTTPException(status_code=404, detail="Guía no encontrada.")

    svc = TrackingService(db)
    await svc.run_full(run_type="manual", tracking_numbers=[shipment.tracking_number])

    await db.refresh(shipment)
    return _to_detail(shipment, [], [])
