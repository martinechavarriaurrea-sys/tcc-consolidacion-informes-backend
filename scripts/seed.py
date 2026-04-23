"""
Script de seed inicial. Pobla email_recipients y app_settings con los valores
definidos en las reglas de negocio. Idempotente: no duplica registros.

Uso:
    python -m scripts.seed
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.database import AsyncSessionLocal
from app.models.email_recipient import EmailRecipient
from app.models.app_setting import AppSetting
from sqlalchemy import select


RECIPIENTS = [
    # Informes diarios
    {"report_type": "daily", "recipient_name": "Angela Maria Diaz Cadavid", "recipient_email": "adiaz@asteco.com.co"},
    {"report_type": "daily", "recipient_name": "Bryan Villada", "recipient_email": "bvillada@asteco.com.co"},
    # Informe semanal
    {"report_type": "weekly", "recipient_name": "Juan Camilo Muñoz", "recipient_email": "jmunoz@asteco.com.co"},
    # Alertas sin movimiento 72h
    {"report_type": "alert_no_movement", "recipient_name": "Juan Camilo Muñoz", "recipient_email": "jmunoz@asteco.com.co"},
    {"report_type": "alert_no_movement", "recipient_name": "Bryan Villada", "recipient_email": "bvillada@asteco.com.co"},
]

SETTINGS = [
    {"key": "app_version", "value": "1.0.0"},
    {"key": "tracking_provider", "value": "tcc"},
    {"key": "alert_no_movement_hours", "value": "72"},
    {"key": "daily_report_hour", "value": "18"},
    {"key": "weekly_report_day", "value": "monday"},
    {"key": "weekly_report_hour", "value": "7"},
    {"key": "max_concurrent_tracking", "value": "5"},
]


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        # Seed recipients
        for data in RECIPIENTS:
            existing = await session.execute(
                select(EmailRecipient).where(
                    EmailRecipient.report_type == data["report_type"],
                    EmailRecipient.recipient_email == data["recipient_email"],
                )
            )
            if existing.scalar_one_or_none() is None:
                session.add(EmailRecipient(**data))
                print(f"  + recipient: {data['report_type']} → {data['recipient_email']}")
            else:
                print(f"  ~ skip (exists): {data['report_type']} → {data['recipient_email']}")

        # Seed settings
        for data in SETTINGS:
            existing = await session.execute(
                select(AppSetting).where(AppSetting.key == data["key"])
            )
            if existing.scalar_one_or_none() is None:
                session.add(AppSetting(**data))
                print(f"  + setting: {data['key']} = {data['value']}")
            else:
                print(f"  ~ skip (exists): {data['key']}")

        await session.commit()
        print("\nSeed completado exitosamente.")


if __name__ == "__main__":
    asyncio.run(seed())
