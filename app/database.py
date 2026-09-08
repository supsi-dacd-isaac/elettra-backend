"""
Database configuration for Elettra.
"""

import os

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.core.config import get_cached_settings


# Initialize database connection
def get_database_url() -> str:
    """Get database URL from settings"""
    settings = get_cached_settings()
    return settings.get_database_url()

# Create async engine
_engine_options = {
    "echo": get_cached_settings().database_echo,
    "future": True,
}
if os.getenv("ELETTRA_TESTING") == "1":
    # asyncpg connections are bound to the event loop on which they were
    # created.  The test suite intentionally exercises the app through both a
    # synchronous TestClient and async tests, which use different loops.
    _engine_options["poolclass"] = NullPool

engine = create_async_engine(get_database_url(), **_engine_options)

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
