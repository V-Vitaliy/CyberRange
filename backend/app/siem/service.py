"""SIEM events: writing them for every domain and reading them for the Blue Team."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import redis_client
from app.db.database import async_session
from app.db.models import GameSession, SecurityAuditLog
from app.siem.repository import AuditLogRepository
from app.siem.schemas import DashboardStatsResponse

logger = logging.getLogger(__name__)

# One AUTH_BYPASS_ATTEMPT per user, claimed role and outcome per window (the default token lifetime).
JWT_FORGERY_LOG_WINDOW_SECONDS = 15 * 60
# Failed logins from one source are counted over this window (EX-07 detection).
LOGIN_ENUMERATION_WINDOW = timedelta(minutes=5)


async def log_security_event(
    lab_instance_id: str, event_type: str, payload: Dict[str, Any], source_ip: str = "127.0.0.1"
) -> Optional[SecurityAuditLog]:
    """
    Core SIEM logging function.
    Records Red Team actions (and system events) into the database
    so the Blue Team can monitor and investigate them.
    The event is written in its own session: it never commits or rolls back the caller's transaction.
    """
    async with async_session() as db:
        return await _write_event(db, lab_instance_id, event_type, payload, source_ip)


async def _write_event(
    db: AsyncSession, lab_instance_id: str, event_type: str, payload: Dict[str, Any], source_ip: str
) -> Optional[SecurityAuditLog]:
    payload.setdefault("source_ip", source_ip)
    try:
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
    lab_instance_id: str, provided_role: str, source_ip: str, *, user_id: str, blocked: bool
):
    """
    Helper function specifically for logging forged JWT usage.
    Repeats within the window are dropped; if Redis is down, every attempt is logged.
    """
    key = f"jwt_forgery:{lab_instance_id}:{user_id}:{provided_role}:{int(blocked)}"
    try:
        reserved = await redis_client.set(key, 1, nx=True, ex=JWT_FORGERY_LOG_WINDOW_SECONDS)
        if not reserved:
            return None
    except Exception:
        reserved = False
        logger.warning("Forgery log de-duplication is unavailable; logging the attempt")
    event = await log_security_event(
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
    if event is None and reserved:
        # The write failed: let the next attempt try again instead of hiding it for the window.
        try:
            await redis_client.delete(key)
        except Exception:
            logger.warning("Could not release the forgery log window after a failed write")
    return event


async def log_failed_login(*, lab_instance_id: str, username: str, source_ip: str, portal: str):
    """LOGIN_FAILED with the number of distinct usernames that recently failed from this source.

    Neither the password nor the failure reason is recorded: the reason would tell
    whether the username exists.
    """
    since = datetime.now(timezone.utc) - LOGIN_ENUMERATION_WINDOW
    async with async_session() as db:
        try:
            # Counting and writing share one transaction locked per lab and source, so concurrent
            # failures from one source wait for each other and every count includes the earlier ones.
            await AuditLogRepository.lock_failed_login_source(db, lab_instance_id, source_ip)
            seen = await AuditLogRepository.recent_failed_login_usernames(db, lab_instance_id, source_ip, since)
            count = len(set(seen) | {username})
        except Exception:
            logger.warning("Could not count recent failed logins; logging the attempt without the count")
            await db.rollback()
            count = None
        payload = {"username": username, "portal": portal, "failed_usernames_from_source": count}
        return await _write_event(db, lab_instance_id, "LOGIN_FAILED", payload, source_ip)


async def get_dashboard_stats(db: AsyncSession, game_session: GameSession) -> DashboardStatsResponse:
    """Aggregates metrics for the Blue Team React Dashboard (KPI cards)."""
    active_defenses = {
        "prompt_hardening_enabled": game_session.system_prompt != "You are a helpful university assistant.",
        "rate_limit_enabled": game_session.rate_limit_enabled,
        "reranker_enabled": game_session.use_reranker,
        "jwt_filter_enabled": game_session.jwt_filter_enabled,
        "login_hardening_enabled": game_session.login_hardening_enabled,
    }

    stats = await AuditLogRepository.get_stats(db, game_session.lab_instance_id)

    return DashboardStatsResponse(budget=game_session.defense_budget, active_defenses=active_defenses, **stats)


async def get_logs(db: AsyncSession, lab_instance_id: str | uuid.UUID, limit: int) -> List[SecurityAuditLog]:
    return await AuditLogRepository.get_logs(db, lab_instance_id, limit)
