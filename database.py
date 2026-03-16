from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings
from models import Base

engine = create_async_engine(
    settings.db_path,
    echo=False,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Миграция: добавить колонки в старую таблицу
        def _migrate(connection):
            cur = connection.execute(
                text("SELECT name FROM pragma_table_info('media') WHERE name='local_path'")
            )
            if cur.fetchone() is None:
                connection.execute(text("ALTER TABLE media ADD COLUMN local_path TEXT"))
            cur = connection.execute(
                text("SELECT name FROM pragma_table_info('media') WHERE name='telegram_file_id'")
            )
            if cur.fetchone() is None:
                connection.execute(text("ALTER TABLE media ADD COLUMN telegram_file_id TEXT"))

        await conn.run_sync(_migrate)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
