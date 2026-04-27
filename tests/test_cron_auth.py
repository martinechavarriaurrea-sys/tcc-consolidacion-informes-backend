import pytest
from httpx import AsyncClient

from app.api.v1 import cron


@pytest.mark.asyncio
async def test_cron_requires_authorization(client: AsyncClient):
    response = await client.get("/api/cron/alerts")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_cron_accepts_configured_secret(client: AsyncClient, monkeypatch):
    called = False

    async def fake_job_check_alerts():
        nonlocal called
        called = True

    monkeypatch.setattr(cron.settings, "cron_secret", "test-secret")
    monkeypatch.setattr(cron, "job_check_alerts", fake_job_check_alerts)

    response = await client.get(
        "/api/cron/alerts",
        headers={"Authorization": "Bearer test-secret"},
    )

    assert response.status_code == 200
    assert response.json()["jobs"] == ["alerts"]
    assert called is True
