from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.shipment import Shipment


@pytest.mark.asyncio
async def test_list_guias_uses_raw_status_when_stored_unknown(
    client: AsyncClient,
    session: AsyncSession,
):
    shipment = Shipment(
        tracking_number="370130864",
        advisor_name="Asesor Test",
        client_name="Cliente Test",
        current_status="desconocido",
        current_status_raw="Envio En Instalaciones Tcc Destino",
        current_status_at=datetime(2026, 5, 2, 10, 57, tzinfo=timezone.utc),
        first_seen_at=datetime(2026, 4, 29, tzinfo=timezone.utc),
        is_active=True,
    )
    session.add(shipment)
    await session.commit()

    response = await client.get("/api/v1/guias?page_size=200")

    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["numero_guia"] == "370130864")
    assert item["estado_actual"] == "en_transito"


@pytest.mark.asyncio
async def test_dashboard_stats_uses_raw_status_when_stored_unknown(
    client: AsyncClient,
    session: AsyncSession,
):
    shipment = Shipment(
        tracking_number="370120663",
        advisor_name="Asesor Test",
        client_name="Cliente Test",
        current_status="desconocido",
        current_status_raw="Reemplazada 472200530",
        current_status_at=datetime(2026, 4, 28, 21, 56, tzinfo=timezone.utc),
        first_seen_at=datetime(2026, 4, 28, tzinfo=timezone.utc),
        is_active=True,
    )
    session.add(shipment)
    await session.commit()

    response = await client.get("/api/v1/dashboard/stats")

    assert response.status_code == 200
    data = response.json()
    item = next(i for i in data["guias_activas"] if i["numero_guia"] == "370120663")
    assert item["estado_actual"] == "novedad"
    assert data["con_novedad"] == 1
