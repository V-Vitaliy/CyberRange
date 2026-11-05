"""SIEM events: writing them for every domain and reading them for the Blue Team."""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import redis_client
from app.db.models import GameSession, SecurityAuditLog
from app.siem.repository import AuditLogRepository
from app.siem.schemas import DashboardStatsResponse

logger = logging.getLogger(__name__)

# One AUTH_BYPASS_ATTEMPT per user, claimed role and outcome per window (the default token lifetime).
JWT_FORGERY_LOG_WINDOW_SECONDS = 15 * 60


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


async def log_jwt_manipulation_attempt(
    db: AsyncSession, lab_instance_id: str, provided_role: str, source_ip: str, *, user_id: str, blocked: bool
):
    """
    Helper function specifically for logging forged JWT usage.
    Repeats within the window are dropped; if Redis is down, every attempt is logged.
    """
    key = f"jwt_forgery:{lab_instance_id}:{user_id}:{provided_role}:{int(blocked)}"
    try:
        if not await redis_client.set(key, 1, nx=True, ex=JWT_FORGERY_LOG_WINDOW_SECONDS):
            return None
    except Exception:
        logger.warning("Forgery log de-duplication is unavailable; logging the attempt")
    return await log_security_event(
        db=db,
        lab_instance_id=lab_instance_id,
        event_type="AUTH_BYPASS_ATTEMPT",
        payload={
            "description": "Suspicious JWT payload detected.",
            "claimed_role": provided_role,
            "blocked": blocked,
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
