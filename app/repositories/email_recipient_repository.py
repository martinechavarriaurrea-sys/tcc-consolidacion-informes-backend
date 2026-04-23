from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_recipient import EmailRecipient
from app.repositories.base import BaseRepository


class EmailRecipientRepository(BaseRepository[EmailRecipient]):
    model = EmailRecipient

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_active_by_type(self, report_type: str) -> list[EmailRecipient]:
        result = await self.session.execute(
            select(EmailRecipient).where(
                EmailRecipient.report_type == report_type,
                EmailRecipient.is_active == True,  # noqa: E712
            )
        )
        return list(result.scalars().all())
