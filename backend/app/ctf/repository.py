"""CTF flags and submissions; other domains use app.ctf.service, not this repository."""

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.models import CtfFlag, CtfSubmission
from app.db.repository import ensure_uuid


class CtfRepository:
    """Handles all database operations for CTF Flags and Submissions."""

    @staticmethod
    async def get_flag_by_value(
        db: AsyncSession, flag_value: str, lab_instance_id: str | uuid.UUID
    ) -> Optional[CtfFlag]:
        """Find a hash only in its owning lab; lab_id stores the canonical lab UUID."""
        result = await db.execute(
            select(CtfFlag).where(
                CtfFlag.flag_value == flag_value,
                CtfFlag.lab_id == str(ensure_uuid(lab_instance_id)),
            )
        )
        return result.scalars().first()

    @staticmethod
    async def has_team_submitted_flag(db: AsyncSession, team_id: str | uuid.UUID, flag_id: int) -> bool:
        result = await db.execute(
            select(CtfSubmission).where(CtfSubmission.team_id == ensure_uuid(team_id), CtfSubmission.flag_id == flag_id)
        )
        return result.scalars().first() is not None

    @staticmethod
    async def submit_flag(db: AsyncSession, team_id: str | uuid.UUID, flag_id: int, lab_instance_id: str | uuid.UUID):
        submission = CtfSubmission(
            team_id=ensure_uuid(team_id), flag_id=flag_id, lab_instance_id=ensure_uuid(lab_instance_id)
        )
        db.add(submission)
        await db.commit()
