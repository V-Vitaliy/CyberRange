from sqlalchemy import text
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

# SIEM events are written through their own pool. A request that holds a connection of `engine`
# while logging waits only for this pool, whose holders never wait for `engine`: no pool deadlock.
siem_engine = create_async_engine(
    DATABASE_URL, pool_size=5, max_overflow=5, pool_timeout=10, pool_pre_ping=True, future=True
)
siem_session = async_sessionmaker(bind=siem_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

# There are no migrations: create_all does not alter existing tables, so columns added to a table
# after its first release are listed here. Each statement must be idempotent.
SCHEMA_UPGRADES = (
    "ALTER TABLE game_sessions ADD COLUMN IF NOT EXISTS login_hardening_enabled BOOLEAN NOT NULL DEFAULT FALSE",
)


async def create_schema(connection) -> None:
    """Create missing tables and apply SCHEMA_UPGRADES on an AsyncConnection."""
    await connection.run_sync(Base.metadata.create_all)
    for statement in SCHEMA_UPGRADES:
        await connection.execute(text(statement))


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
