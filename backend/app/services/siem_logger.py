import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional
import uuid
from app.db.models import SecurityAuditLog

logger = logging.getLogger(__name__)

async def log_security_event(
    db: AsyncSession,
    lab_instance_id: str,
    event_type: str,
    payload: Dict[str, Any],
    source_ip: str = "127.0.0.1"
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
    db: AsyncSession,
    lab_instance_id: str,
    provided_role: str,
    source_ip: str
):
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
            "risk_level": "HIGH"
        },
        source_ip=source_ip
    )