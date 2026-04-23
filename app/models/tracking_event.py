from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ShipmentTrackingEvent(Base):
    __tablename__ = "shipment_tracking_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status_normalized: Mapped[str] = mapped_column(String(50), nullable=False)
    status_raw: Mapped[str] = mapped_column(Text, nullable=False)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    shipment: Mapped["Shipment"] = relationship(back_populates="tracking_events")  # noqa: F821

    __table_args__ = (
        Index("ix_tracking_events_shipment_status", "shipment_id", "status_normalized"),
        Index("ix_tracking_events_event_at", "event_at"),
    )

    def __repr__(self) -> str:
        return f"<TrackingEvent shipment={self.shipment_id} status={self.status_normalized}>"
