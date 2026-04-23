from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ReportFile(Base):
    """Registro de archivos de reporte generados (Excel y PDF).

    Permite trazabilidad completa: qué se generó, cuándo, qué tan grande,
    si el correo fue enviado y cuándo.
    """

    __tablename__ = "report_files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # "daily" | "weekly" | "alert"
    report_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # "xlsx" | "pdf"
    format: Mapped[str] = mapped_column(String(10), nullable=False)

    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Etiqueta del ciclo: "0700" | "1200" | "1600" (solo para tipo daily)
    cycle_label: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Rango de semana (solo para tipo weekly)
    week_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    week_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    email_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_report_files_type_generated", "report_type", "generated_at"),
    )

    def __repr__(self) -> str:
        return f"<ReportFile {self.filename} sent={self.email_sent}>"
