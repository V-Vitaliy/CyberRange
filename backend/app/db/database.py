from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

DATABASE_URL = settings.DATABASE_URL

# Create an asynchronous engine for PostgreSQL
engine = create_async_engine(DATABASE_URL, echo=False, future=True, pool_pre_ping=True)


# SQLAlchemy 2.0 standard base class for all ORM models
class Base(DeclarativeBase):
    pass


# Factory for creating new asynchronous database sessions
async_session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


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
