from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailRecipient(Base):
    __tablename__ = "email_recipients"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # "daily" | "weekly" | "alert_no_movement"
    recipient_name: Mapped[str] = mapped_column(String(200), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(254), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_email_recipients_type_active", "report_type", "is_active"),)

    def __repr__(self) -> str:
        return f"<EmailRecipient {self.recipient_email} type={self.report_type}>"
