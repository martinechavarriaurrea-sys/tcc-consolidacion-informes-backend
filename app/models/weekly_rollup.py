from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WeeklyRollup(Base):
    __tablename__ = "weekly_rollups"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    total_shipments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_in_transit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_with_issues: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_carried_forward: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return f"<WeeklyRollup week={self.week_start}/{self.week_end}>"
