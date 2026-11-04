"""Game sessions of a lab instance; other domains use these functions, not the repository."""

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GameSession
from app.db.repository import ensure_uuid
from app.labs.repository import GameSessionRepository


async def get_session(db: AsyncSession, session_id: str | uuid.UUID, *, for_update: bool = False) -> GameSession | None:
    return await GameSessionRepository.get_by_id(db, session_id, for_update=for_update)


async def get_session_for_lab(db: AsyncSession, lab_instance_id: str | uuid.UUID) -> GameSession | None:
    return await GameSessionRepository.get_by_lab_instance_id(db, lab_instance_id)


async def get_authorized_session(db, session_id, lab_instance_id):
    try:
        session = await GameSessionRepository.get_by_id(db, session_id)
        lab_id = ensure_uuid(lab_instance_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="Invalid session or lab ID")
    if session is None or session.lab_instance_id != lab_id:
        raise HTTPException(status_code=404, detail="Game session not found")
    return session
