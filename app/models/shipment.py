from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Shipment(Base):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tracking_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    advisor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    package_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(300), nullable=True)
    shipping_date: Mapped["date | None"] = mapped_column(Date, nullable=True)

    # Estado normalizado (categoría) y estado raw (exacto de TCC)
    current_status: Mapped[str] = mapped_column(String(50), nullable=False, default="desconocido")
    current_status_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    tracking_events: Mapped[list["ShipmentTrackingEvent"]] = relationship(  # noqa: F821
        back_populates="shipment", cascade="all, delete-orphan", lazy="select"
    )
    alert_events: Mapped[list["AlertEvent"]] = relationship(  # noqa: F821
        back_populates="shipment", cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        Index("ix_shipments_is_active", "is_active"),
        Index("ix_shipments_current_status", "current_status"),
        Index("ix_shipments_advisor_name", "advisor_name"),
    )

    def __repr__(self) -> str:
        return f"<Shipment {self.tracking_number} status={self.current_status}>"
