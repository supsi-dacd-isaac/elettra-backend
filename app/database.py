"""
Database configuration for Elettra.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from app.core.config import get_cached_settings


# Initialize database connection
def get_database_url() -> str:
    """Get database URL from settings"""
    settings = get_cached_settings()
    return settings.get_database_url()

# Create async engine
engine = create_async_engine(
    get_database_url(),
    echo=get_cached_settings().database_echo,
    future=True
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False
)

async def get_async_session() -> AsyncSession:
    """Dependency to get async database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
