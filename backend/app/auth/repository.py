"""User accounts; other domains use app.auth.service, not this repository."""

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.ids import ensure_uuid
from app.db.models import User


class UserRepository:
    """Handles all database operations for Users."""

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> Optional[User]:
        result = await db.execute(select(User).where(User.id == ensure_uuid(user_id)))
        return result.scalars().first()

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.username == username))
        return result.scalars().first()

    @staticmethod
    async def add(db: AsyncSession, **values) -> User:
        user = User(**values)
        db.add(user)
        await db.flush()
        return user
