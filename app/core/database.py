import os
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()


def _resolve_database_url(raw_url: str) -> str:
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgresql://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Neon/libpq URLs include sslmode and channel_binding params.
    # asyncpg expects `ssl` and does not accept `channel_binding`.
    if raw_url.startswith("postgresql+asyncpg://"):
        parsed = urlparse(raw_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if "sslmode" in query:
            query["ssl"] = "require" if query["sslmode"] == "require" else query["sslmode"]
            query.pop("sslmode", None)
        query.pop("channel_binding", None)
        raw_url = urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(query),
                parsed.fragment,
            )
        )

    # On Vercel, the deployment bundle is read-only. Copy bundled sqlite DB to /tmp
    # so API writes can work within the warm instance lifecycle.
    if os.getenv("VERCEL") and raw_url.startswith("sqlite+aiosqlite:///./"):
        relative_db = raw_url.replace("sqlite+aiosqlite:///./", "", 1)
        source_db = Path(__file__).resolve().parents[2] / relative_db
        target_db = Path("/tmp") / Path(relative_db).name
        if source_db.exists() and not target_db.exists():
            shutil.copy2(source_db, target_db)
        return f"sqlite+aiosqlite:///{target_db.as_posix()}"
    return raw_url


database_url = _resolve_database_url(settings.database_url)
_is_sqlite = database_url.startswith("sqlite")
engine = create_async_engine(
    database_url,
    echo=settings.database_echo,
    **({} if _is_sqlite else {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True}),
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
