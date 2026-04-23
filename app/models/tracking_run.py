from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TrackingRun(Base):
    __tablename__ = "tracking_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "scheduled" | "manual"
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # "running" | "completed" | "partial" | "failed"
    shipments_checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shipments_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shipments_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<TrackingRun id={self.id} type={self.run_type} status={self.status}>"
