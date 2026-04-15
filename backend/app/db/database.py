from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Fetch the database URL from the central configuration.
# If not yet defined in config.py, it falls back to the local Docker setup.
DATABASE_URL = getattr(
    settings,
    "DATABASE_URL",
    "postgresql+asyncpg://cyberadmin:cyberpassword123@127.0.0.1:5432/cyberrange"
)

# Create an asynchronous engine for PostgreSQL
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True
)

# SQLAlchemy 2.0 standard base class for all ORM models
class Base(DeclarativeBase):
    pass

# Factory for creating new asynchronous database sessions
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def get_db():
    """
    FastAPI dependency to provide a database session per request.
    Safely yields the session and ensures it is closed after the request finishes.
    """
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()