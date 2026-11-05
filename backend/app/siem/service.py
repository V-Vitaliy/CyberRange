"""SIEM events: writing them for every domain and reading them for the Blue Team."""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GameSession, SecurityAuditLog
from app.siem.repository import AuditLogRepository
from app.siem.schemas import DashboardStatsResponse

logger = logging.getLogger(__name__)


async def log_security_event(
    db: AsyncSession, lab_instance_id: str, event_type: str, payload: Dict[str, Any], source_ip: str = "127.0.0.1"
) -> Optional[SecurityAuditLog]:
    """
    Core SIEM logging function.
    Records Red Team actions (and system events) into the database
    so the Blue Team can monitor and investigate them.
    """
    try:
        if "source_ip" not in payload:
            payload["source_ip"] = source_ip

        # Create a new audit log record
        new_event = SecurityAuditLog(
            lab_instance_id=lab_instance_id,
            timestamp=datetime.utcnow(),
            event_type=event_type,
            payload=payload,
        )

        db.add(new_event)
        await db.commit()
        await db.refresh(new_event)

        return new_event

    except Exception as e:
        await db.rollback()
        logger.error(f"SIEM Logger failed to record event '{event_type}': {str(e)}")
        return None


async def log_jwt_manipulation_attempt(db: AsyncSession, lab_instance_id: str, provided_role: str, source_ip: str):
    """
    Helper function specifically for logging forged JWT usage.
    """
    return await log_security_event(
        db=db,
        lab_instance_id=lab_instance_id,
        event_type="AUTH_BYPASS_ATTEMPT",
        payload={
            "description": "Suspicious JWT payload detected.",
            "claimed_role": provided_role,
            "risk_level": "HIGH",
        },
        source_ip=source_ip,
    )


async def get_dashboard_stats(db: AsyncSession, game_session: GameSession) -> DashboardStatsResponse:
    """Aggregates metrics for the Blue Team React Dashboard (KPI cards)."""
    active_defenses = {
        "prompt_hardening_enabled": game_session.system_prompt != "You are a helpful university assistant.",
        "rate_limit_enabled": game_session.rate_limit_enabled,
        "reranker_enabled": game_session.use_reranker,
        "jwt_filter_enabled": game_session.jwt_filter_enabled,
    }

    stats = await AuditLogRepository.get_stats(db, game_session.lab_instance_id)

    return DashboardStatsResponse(budget=game_session.defense_budget, active_defenses=active_defenses, **stats)


async def get_logs(db: AsyncSession, lab_instance_id: str | uuid.UUID, limit: int) -> List[SecurityAuditLog]:
    return await AuditLogRepository.get_logs(db, lab_instance_id, limit)
