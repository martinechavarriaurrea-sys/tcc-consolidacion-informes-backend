from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ShipmentCreate(BaseModel):
    tracking_number: str = Field(..., min_length=1, max_length=100)
    advisor_name: str = Field(..., min_length=1, max_length=200)
    client_name: str | None = Field(None, max_length=200)
    package_type: str | None = Field(None, max_length=100)
    destination: str | None = Field(None, max_length=300)
    shipping_date: date | None = Field(None, description="Fecha en que se despachó la guía")

    @field_validator("tracking_number")
    @classmethod
    def normalize_tracking_number(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("advisor_name")
    @classmethod
    def normalize_advisor(cls, v: str) -> str:
        return v.strip().title()


class ShipmentUpdate(BaseModel):
    advisor_name: str | None = Field(None, max_length=200)
    client_name: str | None = Field(None, max_length=200)
    package_type: str | None = Field(None, max_length=100)
    destination: str | None = Field(None, max_length=300)
    shipping_date: date | None = Field(None)


class TrackingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status_normalized: str
    status_raw: str
    event_at: datetime | None
    observed_at: datetime
    notes: str | None
    payload_snapshot: dict[str, Any] | None
    created_at: datetime


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tracking_number: str
    advisor_name: str
    client_name: str | None
    package_type: str | None
    destination: str | None
    current_status: str
    current_status_raw: str | None
    current_status_at: datetime | None
    first_seen_at: datetime
    shipping_date: date | None
    delivered_at: datetime | None
    closed_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ShipmentDetailOut(ShipmentOut):
    tracking_events: list[TrackingEventOut] = []


class ShipmentListFilters(BaseModel):
    is_active: bool | None = None
    current_status: str | None = None
    advisor_name: str | None = None
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)
