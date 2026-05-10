import uuid
import datetime
from typing import List, Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc, func
from sqlalchemy.orm.attributes import flag_modified

from app.db.models import User, GameSession, SecurityAuditLog, ChatThread, CtfFlag, CtfSubmission

def ensure_uuid(val: str | uuid.UUID) -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


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


class GameSessionRepository:
    """Handles all database operations for Game Sessions."""

    @staticmethod
    async def get_by_id(db: AsyncSession, session_id: str | uuid.UUID) -> Optional[GameSession]:
        result = await db.execute(select(GameSession).where(GameSession.id == ensure_uuid(session_id)))
        return result.scalars().first()

    @staticmethod
    async def get_by_lab_instance_id(db: AsyncSession, lab_instance_id: str | uuid.UUID) -> Optional[GameSession]:
        result = await db.execute(select(GameSession).where(GameSession.lab_instance_id == ensure_uuid(lab_instance_id)))
        return result.scalars().first()


class ChatRepository:
    """Handles all database operations for Chat Threads (Red Team UI)."""

    @staticmethod
    async def get_threads_by_user_and_session(
        db: AsyncSession, user_id: str | uuid.UUID, session_id: str | uuid.UUID
    ) -> List[ChatThread]:
        """Fetches chat history for the left sidebar."""
        result = await db.execute(
            select(ChatThread)
            .where(ChatThread.user_id == ensure_uuid(user_id), ChatThread.session_id == ensure_uuid(session_id))
            .order_by(desc(ChatThread.updated_at))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_or_create_thread(
        db: AsyncSession,
        thread_id: Optional[str | uuid.UUID],
        user_id: str | uuid.UUID,
        session_id: str | uuid.UUID,
        initial_prompt: str
    ) -> ChatThread:
        """Retrieves an existing thread or creates a new one if it doesn't exist."""
        if thread_id:
            result = await db.execute(select(ChatThread).where(ChatThread.id == ensure_uuid(thread_id)))
            thread = result.scalars().first()
            if thread:
                return thread

        # Create a new thread
        new_thread = ChatThread(
            id=ensure_uuid(thread_id) if thread_id else uuid.uuid4(),
            user_id=ensure_uuid(user_id),
            session_id=ensure_uuid(session_id),
            title=initial_prompt[:30] + "..." if len(initial_prompt) > 30 else initial_prompt,
            messages=[]
        )
        db.add(new_thread)
        await db.commit()
        await db.refresh(new_thread)
        return new_thread

    @staticmethod
    async def append_messages(
        db: AsyncSession,
        thread_id: str | uuid.UUID,
        prompt: str,
        ai_response: str
    ):
        """Appends a new user prompt and AI response to the thread's JSONB message list."""
        result = await db.execute(select(ChatThread).where(ChatThread.id == ensure_uuid(thread_id)))
        thread = result.scalars().first()

        if thread:
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

            new_messages = [
                {"role": "user", "content": prompt, "timestamp": timestamp},
                {"role": "ai", "content": ai_response, "timestamp": timestamp}
            ]

            if not isinstance(thread.messages, list):
                thread.messages = []

            thread.messages.extend(new_messages)

            flag_modified(thread, "messages")
            await db.commit()


class AuditLogRepository:
    """Handles all database operations for SIEM Logs (Blue Team UI)."""

    @staticmethod
    async def get_by_id(db: AsyncSession, log_id: uuid.UUID | str):
        result = await db.execute(select(SecurityAuditLog).where(SecurityAuditLog.id == ensure_uuid(log_id)))
        return result.scalars().first()

    @staticmethod
    async def get_stats(db: AsyncSession, lab_instance_id: str | uuid.UUID) -> dict:
        """Aggregates statistics for the Blue Team Dashboard."""
        lab_uuid = ensure_uuid(lab_instance_id)

        total_res = await db.execute(select(func.count(SecurityAuditLog.id)).where(SecurityAuditLog.lab_instance_id == lab_uuid))
        total_logs = total_res.scalar() or 0

        comp_res = await db.execute(select(func.count(SecurityAuditLog.id)).where(SecurityAuditLog.lab_instance_id == lab_uuid, SecurityAuditLog.is_compromised == True))
        compromised_logs = comp_res.scalar() or 0

        pend_res = await db.execute(select(func.count(SecurityAuditLog.id)).where(SecurityAuditLog.lab_instance_id == lab_uuid, SecurityAuditLog.investigated_at.is_(None)))
        pending_investigations = pend_res.scalar() or 0

        return {
            "total_logs": total_logs,
            "compromised_logs": compromised_logs,
            "pending_investigations": pending_investigations
        }

    @staticmethod
    async def get_logs(db: AsyncSession, lab_instance_id: str | uuid.UUID, limit: int = 50) -> List[SecurityAuditLog]:
        """Fetches raw logs for the Blue Team terminal."""
        result = await db.execute(
            select(SecurityAuditLog)
            .where(SecurityAuditLog.lab_instance_id == ensure_uuid(lab_instance_id))
            .order_by(desc(SecurityAuditLog.timestamp))
            .limit(limit)
        )
        return list(result.scalars().all())


class CtfRepository:
    """Handles all database operations for CTF Flags and Submissions."""

    @staticmethod
    async def get_flag_by_value(db: AsyncSession, flag_value: str) -> Optional[CtfFlag]:
        result = await db.execute(select(CtfFlag).where(CtfFlag.flag_value == flag_value))
        return result.scalars().first()

    @staticmethod
    async def has_team_submitted_flag(db: AsyncSession, team_id: str | uuid.UUID, flag_id: int) -> bool:
        result = await db.execute(
            select(CtfSubmission).where(
                CtfSubmission.team_id == ensure_uuid(team_id),
                CtfSubmission.flag_id == flag_id
            )
        )
        return result.scalars().first() is not None

    @staticmethod
    async def submit_flag(db: AsyncSession, team_id: str | uuid.UUID, flag_id: int, lab_instance_id: str | uuid.UUID):
        submission = CtfSubmission(
            team_id=ensure_uuid(team_id),
            flag_id=flag_id,
            lab_instance_id=ensure_uuid(lab_instance_id)
        )
        db.add(submission)
        await db.commit()