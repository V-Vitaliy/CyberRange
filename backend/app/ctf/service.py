"""Flag submission for Red Team players."""

import hashlib

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ctf.repository import CtfRepository
from app.labs.service import get_session_for_lab
from app.services.siem_logger import log_security_event


def hash_ctf_flag(flag_value: str) -> str:
    """Hashes a CTF flag value using SHA-256 for secure database storage and comparison."""
    return hashlib.sha256(flag_value.encode("utf-8")).hexdigest()


async def submit_flag(db: AsyncSession, user: dict, flag_value: str) -> dict:
    """Credit a flag of the player's own lab to their team, once."""
    user_id = user.get("id")
    lab_instance_id = user.get("lab_instance_id")

    if not user_id or not lab_instance_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")

    game_session = await get_session_for_lab(db, lab_instance_id)

    if not game_session:
        raise HTTPException(status_code=404, detail="Game session not found for this lab instance.")

    team_id = game_session.team_id

    hashed_submission = hash_ctf_flag(flag_value)

    flag = await CtfRepository.get_flag_by_value(db, hashed_submission, lab_instance_id)

    if not flag:
        await log_security_event(
            db=db,
            lab_instance_id=lab_instance_id,
            event_type="CTF_SUBMIT_FAILED",
            payload={"attempted_flag": flag_value},
            source_ip="CTF_ENGINE",
        )
        raise HTTPException(status_code=400, detail="Invalid flag.")

    already_submitted = await CtfRepository.has_team_submitted_flag(db, team_id=team_id, flag_id=flag.id)

    if already_submitted:
        raise HTTPException(status_code=400, detail="Flag already captured by your team.")

    await CtfRepository.submit_flag(db, team_id=team_id, flag_id=flag.id, lab_instance_id=lab_instance_id)

    await log_security_event(
        db=db,
        lab_instance_id=lab_instance_id,
        event_type="CTF_FLAG_CAPTURED",
        payload={"flag_id": flag.id, "reward": flag.reward, "submitted_by": user_id},
        source_ip="CTF_ENGINE",
    )

    return {
        "success": True,
        "message": f"Flag captured successfully! You earned {flag.reward} points.",
        "reward": flag.reward,
    }
