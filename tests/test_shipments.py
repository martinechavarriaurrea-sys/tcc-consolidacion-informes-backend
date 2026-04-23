import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_shipment(client: AsyncClient):
    response = await client.post(
        "/api/v1/shipments",
        json={"tracking_number": "TCC123456", "advisor_name": "Bryan Villada"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["tracking_number"] == "TCC123456"
    assert data["advisor_name"] == "Bryan Villada"
    assert data["is_active"] is True
    assert data["current_status"] == "registrado"


@pytest.mark.asyncio
async def test_create_duplicate_shipment(client: AsyncClient):
    await client.post(
        "/api/v1/shipments",
        json={"tracking_number": "TCC-DUP-001", "advisor_name": "Asesor Test"},
    )
    response = await client.post(
        "/api/v1/shipments",
        json={"tracking_number": "TCC-DUP-001", "advisor_name": "Asesor Test"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_list_shipments(client: AsyncClient):
    response = await client.get("/api/v1/shipments")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "items" in data
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_get_shipment_not_found(client: AsyncClient):
    response = await client.get("/api/v1/shipments/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_shipment_detail(client: AsyncClient):
    create = await client.post(
        "/api/v1/shipments",
        json={"tracking_number": "TCC-DETAIL-001", "advisor_name": "Asesor Test"},
    )
    shipment_id = create.json()["id"]
    response = await client.get(f"/api/v1/shipments/{shipment_id}")
    assert response.status_code == 200
    assert "tracking_events" in response.json()


@pytest.mark.asyncio
async def test_update_shipment(client: AsyncClient):
    create = await client.post(
        "/api/v1/shipments",
        json={"tracking_number": "TCC-UPDATE-001", "advisor_name": "Asesor Original"},
    )
    shipment_id = create.json()["id"]
    response = await client.patch(
        f"/api/v1/shipments/{shipment_id}",
        json={"client_name": "Cliente Nuevo"},
    )
    assert response.status_code == 200
    assert response.json()["client_name"] == "Cliente Nuevo"


@pytest.mark.asyncio
async def test_close_shipment(client: AsyncClient):
    create = await client.post(
        "/api/v1/shipments",
        json={"tracking_number": "TCC-CLOSE-001", "advisor_name": "Asesor Test"},
    )
    shipment_id = create.json()["id"]
    response = await client.post(f"/api/v1/shipments/{shipment_id}/close")
    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_tracking_number_normalized_to_uppercase(client: AsyncClient):
    response = await client.post(
        "/api/v1/shipments",
        json={"tracking_number": "tcc-lower-001", "advisor_name": "Test"},
    )
    assert response.status_code == 201
    assert response.json()["tracking_number"] == "TCC-LOWER-001"
