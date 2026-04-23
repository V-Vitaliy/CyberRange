from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from redis.asyncio import Redis
from uuid import UUID

from app.db.models import GameSession
from app.services.siem_logger import log_security_event


async def enforce_rate_limit(
    db: AsyncSession,
    redis: Redis,
    session_id: UUID,
    client_ip: str
) -> None:
    result = await db.execute(select(GameSession).where(GameSession.id == session_id))
    session = result.scalars().first()

    if not session or not session.rate_limit_enabled:
        print(f"DEBUG: Rate limit disabled or session {session_id} not found.")
        return

    redis_key = f"rate_limit:{session_id}:{client_ip}"

    current_requests = await redis.incr(redis_key)

    print(f"DEBUG: IP {client_ip} has {current_requests} requests. Limit: {session.rate_limit_rpm}")

    if current_requests == 1:
        await redis.expire(redis_key, 60)

    if current_requests > session.rate_limit_rpm:
        print(f"DEBUG: IP {client_ip} BLOCKED!")

        # Log the DoS attempt into SIEM
        await log_security_event(
            db=db,
            lab_instance_id=str(session.lab_instance_id),
            event_type="DOS_ATTEMPT_BLOCKED",
            payload={
                "current_requests": current_requests,
                "limit": session.rate_limit_rpm,
                "description": "Rate limit exceeded. Potential DoS or Sponge Bomb attack."
            },
            source_ip=client_ip
        )

        raise HTTPException(
            status_code=429,
            detail="Too Many Requests. The Blue Team's Active WAF has temporarily blocked your IP."
        )