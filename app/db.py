from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import get_cached_settings

def get_database_config():
    """Get database configuration from settings"""
    settings = get_cached_settings()
    return settings.database_url, settings.database_echo

DATABASE_URL, DATABASE_ECHO = get_database_config()

engine = create_async_engine(DATABASE_URL, echo=DATABASE_ECHO, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session
