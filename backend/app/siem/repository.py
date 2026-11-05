"""SIEM audit log reads; other domains use app.siem.service, not this repository."""

import uuid
from typing import List

from sqlalchemy import desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.ids import ensure_uuid
from app.db.models import SecurityAuditLog


class AuditLogRepository:
    """Handles all database operations for SIEM Logs (Blue Team UI)."""

    @staticmethod
    async def get_by_id(db: AsyncSession, log_id: uuid.UUID | str, *, for_update=False):
        stmt = select(SecurityAuditLog).where(SecurityAuditLog.id == ensure_uuid(log_id))
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def get_stats(db: AsyncSession, lab_instance_id: str | uuid.UUID) -> dict:
        """Aggregates statistics for the Blue Team Dashboard."""
        lab_uuid = ensure_uuid(lab_instance_id)

        total_res = await db.execute(
            select(func.count(SecurityAuditLog.id)).where(SecurityAuditLog.lab_instance_id == lab_uuid)
        )
        total_logs = total_res.scalar() or 0

        comp_res = await db.execute(
            select(func.count(SecurityAuditLog.id)).where(
                SecurityAuditLog.lab_instance_id == lab_uuid, SecurityAuditLog.is_compromised.is_(True)
            )
        )
        compromised_logs = comp_res.scalar() or 0

        pend_res = await db.execute(
            select(func.count(SecurityAuditLog.id)).where(
                SecurityAuditLog.lab_instance_id == lab_uuid, SecurityAuditLog.investigated_at.is_(None)
            )
        )
        pending_investigations = pend_res.scalar() or 0

        return {
            "total_logs": total_logs,
            "compromised_logs": compromised_logs,
            "pending_investigations": pending_investigations,
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
