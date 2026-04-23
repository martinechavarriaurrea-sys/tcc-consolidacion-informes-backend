from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TrackingRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_type: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    shipments_checked: int
    shipments_updated: int
    shipments_failed: int
    error_summary: str | None


class ManualRunRequest(BaseModel):
    tracking_numbers: list[str] | None = None  # None = todos los activos


class ProviderEventResult(BaseModel):
    status_raw: str
    status_normalized: str
    event_at: datetime | None
    observed_at: datetime
    notes: str | None = None


class TrackingResult(BaseModel):
    tracking_number: str
    current_status_raw: str | None = None
    current_status_normalized: str | None = None
    current_status_at: datetime | None = None
    destination: str | None = None
    package_type: str | None = None
    client_name: str | None = None
    events: list[ProviderEventResult] = []
    payload_snapshot: dict[str, Any] = {}
    fetch_success: bool
    fetch_error: str | None = None
