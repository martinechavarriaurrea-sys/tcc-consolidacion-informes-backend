"""
Carga las guias Angela Diaz, registra en BD y consulta TCC para cada una.
"""
import asyncio
import sys

sys.path.insert(0, ".")

from app.core.database import AsyncSessionLocal
from app.core.exceptions import DuplicateError
from app.models.shipment import Shipment
from app.schemas.shipment import ShipmentCreate
from app.services.shipment_service import ShipmentService
from app.services.tracking_service import TrackingService
from app.utils.date_utils import utcnow

GUIAS = [
    ("370029693", "SUMIWELD"),
    ("370032293", "COMERCIAL DE HERRAMIENTAS"),
    ("370032432", "SAGER CALI"),
    ("370032540", "HERRAJES ANDINA"),
    ("370032779", "DAVID SONZA"),
    ("370033557", "DAVID AHUMADA"),
    ("370033940", "CENTRAL DE SOLDADURAS"),
    ("370034009", "IMPLEMENTOS IND TECNICOS"),
    ("370034728", "TECNISERVICIOS NEGRET"),
    ("370035625", "ETEC SAS"),
    ("370035682", "DISEÑO MECANIZADO"),
    ("140024761", "PLAST INNOVA"),
    ("140022733", "SYH IMPORTACIONES"),
]

ASESOR = "Angela Diaz"


async def main():
    print(f"\n{'='*55}")
    print(f"  Cargando {len(GUIAS)} guias - Asesor: {ASESOR}")
    print(f"{'='*55}\n")

    registradas = []

    async with AsyncSessionLocal() as session:
        svc = ShipmentService(session)
        for numero, cliente in GUIAS:
            try:
                shipment = await svc.create(ShipmentCreate(
                    tracking_number=numero,
                    advisor_name=ASESOR,
                    client_name=cliente,
                ))
                registradas.append(numero)
                print(f"  [OK]  Registrada  {numero} - {cliente}")
            except DuplicateError:
                print(f"  [--]  Ya existe   {numero} - {cliente}")
                registradas.append(numero)
            except Exception as e:
                print(f"  [ERR] Error        {numero}: {e}")
        await session.commit()

    print(f"\n{'='*55}")
    print(f"  Consultando TCC para {len(registradas)} guias...")
    print(f"{'='*55}\n")

    async with AsyncSessionLocal() as session:
        tracking_svc = TrackingService(session)
        run = await tracking_svc.run_full(
            run_type="manual",
            tracking_numbers=registradas,
        )

    print(f"\n{'='*55}")
    print(f"  Resultado TCC:")
    print(f"    Consultadas : {run.shipments_checked}")
    print(f"    Actualizadas: {run.shipments_updated}")
    print(f"    Fallidas    : {run.shipments_failed}")
    if run.error_summary:
        print(f"    Errores     : {run.error_summary}")
    print(f"{'='*55}\n")

    # Mostrar estado final de cada guia
    from sqlalchemy import select
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Shipment).where(
                Shipment.tracking_number.in_(registradas)
            ).order_by(Shipment.tracking_number)
        )
        shipments = result.scalars().all()
        print(f"  {'GUÍA':<15} {'CLIENTE':<30} {'ESTADO'}")
        print(f"  {'-'*15} {'-'*30} {'-'*20}")
        for s in shipments:
            estado = s.current_status_raw or s.current_status or "registrado"
            print(f"  {s.tracking_number:<15} {(s.client_name or ''):<30} {estado}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
