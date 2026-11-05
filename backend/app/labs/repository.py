import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.ids import ensure_uuid
from app.db.models import GameSession


class GameSessionRepository:
    """Handles all database operations for Game Sessions."""

    @staticmethod
    async def get_by_id(db: AsyncSession, session_id: str | uuid.UUID, *, for_update=False) -> Optional[GameSession]:
        stmt = select(GameSession).where(GameSession.id == ensure_uuid(session_id))
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_by_lab_instance_id(db: AsyncSession, lab_instance_id: str | uuid.UUID) -> Optional[GameSession]:
        result = await db.execute(
            select(GameSession).where(GameSession.lab_instance_id == ensure_uuid(lab_instance_id))
        )
        return result.scalars().first()
