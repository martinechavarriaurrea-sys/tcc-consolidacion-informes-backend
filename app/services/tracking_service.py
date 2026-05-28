"""Orquesta el ciclo completo de tracking:
1. Trae guias activas (o una lista especifica)
2. Consulta TCC por cada una
3. Almacena eventos nuevos
4. Actualiza estado en shipment
5. Marca entregadas / lanza alertas
6. Registra la ejecucion en tracking_runs
7. Detecta guias reemplazadas y crea automaticamente el nuevo registro
"""

from __future__ import annotations

import asyncio
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.integrations.tcc.base import TrackingResult as IntegrationResult
from app.integrations.tcc.client import get_tcc_client
from app.models.shipment import Shipment
from app.models.tracking_event import ShipmentTrackingEvent
from app.models.tracking_run import TrackingRun
from app.repositories.shipment_repository import ShipmentRepository
from app.repositories.tracking_event_repository import TrackingEventRepository
from app.repositories.tracking_run_repository import TrackingRunRepository
from app.services.alert_service import AlertService
from app.utils.date_utils import utcnow
from app.utils.status_normalizer import NormalizedStatus, normalize_status

# Detecta "Reemplazada 472190991" o "Reemplazado por 472190991"
_REPLACEMENT_RE = re.compile(r"reemplaz[a-z]*\s+(?:por\s+)?(\d{6,12})", re.IGNORECASE)

logger = get_logger(__name__)
settings = get_settings()

_MAX_CONCURRENT = 5  # Limite de peticiones concurrentes a TCC.


class TrackingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.shipment_repo = ShipmentRepository(session)
        self.event_repo = TrackingEventRepository(session)
        self.run_repo = TrackingRunRepository(session)

    async def run_full(
        self,
        run_type: str = "scheduled",
        tracking_numbers: list[str] | None = None,
    ) -> TrackingRun:
        run = TrackingRun(run_type=run_type, started_at=utcnow(), status="running")
        run = await self.run_repo.add(run)

        if tracking_numbers:
            shipments = []
            for tn in tracking_numbers:
                shipment = await self.shipment_repo.get_by_tracking_number(tn)
                if shipment:
                    shipments.append(shipment)
        else:
            shipments = await self.shipment_repo.get_active()

        logger.info("tracking_run_start", run_id=run.id, count=len(shipments), type=run_type)
        checked = updated = failed = 0
        errors: list[str] = []

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
        provider = get_tcc_client()

        async def _process(shipment: Shipment) -> tuple[bool, bool]:
            async with semaphore:
                return await self._process_one(shipment, provider)

        results = await asyncio.gather(*[_process(shipment) for shipment in shipments], return_exceptions=True)

        for shipment, result in zip(shipments, results):
            checked += 1
            if isinstance(result, Exception):
                failed += 1
                errors.append(f"{shipment.tracking_number}: {result}")
                logger.error("tracking_run_shipment_error", tracking=shipment.tracking_number, exc=str(result))
            else:
                success, was_updated = result
                if not success:
                    failed += 1
                    errors.append(shipment.tracking_number)
                elif was_updated:
                    updated += 1

        run.finished_at = utcnow()
        run.status = "completed" if failed == 0 else "partial" if updated > 0 else "failed"
        run.shipments_checked = checked
        run.shipments_updated = updated
        run.shipments_failed = failed
        run.error_summary = "; ".join(errors[:20]) if errors else None

        logger.info(
            "tracking_run_done",
            run_id=run.id,
            checked=checked,
            updated=updated,
            failed=failed,
        )
        return run

    async def _process_one(self, shipment: Shipment, provider) -> tuple[bool, bool]:
        result: IntegrationResult = await provider.fetch(shipment.tracking_number)
        return await self.apply_result(shipment, result, provider=provider)

    async def apply_result(self, shipment: Shipment, result: IntegrationResult, *, provider=None) -> tuple[bool, bool]:
        if not result.fetch_success or not result.events:
            logger.warning(
                "tracking_fetch_failed",
                tracking=shipment.tracking_number,
                provider=result.provider,
                fetch_error=result.fetch_error,
            )
            return False, False

        was_updated = False
        for event_data in result.events:
            status_norm = event_data.status_normalized or normalize_status(event_data.status_raw).value
            already_exists = await self.event_repo.status_exists(shipment.id, event_data.status_raw)
            if already_exists:
                continue

            event = ShipmentTrackingEvent(
                shipment_id=shipment.id,
                status_normalized=status_norm,
                status_raw=event_data.status_raw,
                event_at=event_data.event_at,
                observed_at=event_data.observed_at or utcnow(),
                notes=event_data.notes,
                payload_snapshot=event_data.payload_snapshot or result.payload_snapshot or None,
            )
            await self.event_repo.add(event)
            was_updated = True

        latest = result.latest_event
        if latest:
            new_status_raw = result.current_status_raw or latest.status_raw
            new_status_norm = (
                result.current_status_normalized
                or latest.status_normalized
                or normalize_status(new_status_raw).value
            )
            new_status_at = result.current_status_at or latest.event_at or utcnow()

            # Normalizar timezone para comparacion segura
            from datetime import timezone as _tz
            def _naive(dt):
                if dt is None:
                    return None
                return dt.replace(tzinfo=None) if dt.tzinfo else dt

            if (
                was_updated
                or shipment.current_status_raw != new_status_raw
                or shipment.current_status != new_status_norm
                or _naive(shipment.current_status_at) != _naive(new_status_at)
            ):
                shipment.current_status = new_status_norm
                shipment.current_status_raw = new_status_raw
                shipment.current_status_at = _naive(new_status_at)
                shipment.updated_at = utcnow()

            if new_status_norm == NormalizedStatus.ENTREGADO and not shipment.delivered_at:
                shipment.delivered_at = _naive(new_status_at)
                shipment.is_active = False
                await AlertService(self.session).resolve_all_for_shipment(shipment.id)
                logger.info("shipment_delivered", tracking=shipment.tracking_number)

            # Detectar reemplazo de guía por TCC
            replacement_match = _REPLACEMENT_RE.search(new_status_raw or "")
            if replacement_match and shipment.is_active:
                new_number = replacement_match.group(1)
                await self._handle_replacement(shipment, new_number, provider=provider)
                was_updated = True

        return True, was_updated

    async def _handle_replacement(self, shipment: Shipment, new_tracking_number: str, *, provider=None) -> None:
        """Actualiza el número de guía en el mismo registro y consulta TCC con el número nuevo.
        Mantiene cliente, asesor y fecha de despacho intactos."""
        old_number = shipment.tracking_number

        # Si el número nuevo ya existe como registro separado, fusionar: usar ese registro
        existing = await self.shipment_repo.get_by_tracking_number(new_tracking_number)
        if existing:
            # Ya rastreado por separado — no duplicar, solo cerrar el viejo
            shipment.is_active = False
            shipment.closed_at = utcnow()
            shipment.current_status = NormalizedStatus.REEMPLAZADO
            shipment.updated_at = utcnow()
            await AlertService(self.session).resolve_all_for_shipment(shipment.id)
            logger.info("shipment_replaced_already_exists", old=old_number, existing=new_tracking_number)
            return

        # Actualizar el número de guía en el mismo registro (mantiene cliente/asesor/fecha)
        shipment.tracking_number = new_tracking_number
        shipment.is_active = True
        shipment.current_status = "registrado"
        shipment.current_status_raw = None
        shipment.current_status_at = None
        shipment.updated_at = utcnow()
        await AlertService(self.session).resolve_all_for_shipment(shipment.id)
        logger.info("shipment_number_updated", old=old_number, new=new_tracking_number)

        # Consultar TCC inmediatamente con el número nuevo para obtener su estado real
        if provider:
            try:
                new_result: IntegrationResult = await provider.fetch(new_tracking_number)
                if new_result.fetch_success and new_result.events:
                    await self.apply_result(shipment, new_result)
            except Exception as exc:
                logger.warning("replacement_tcc_fetch_failed", new=new_tracking_number, exc=str(exc))
