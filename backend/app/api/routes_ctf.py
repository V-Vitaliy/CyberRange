from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.core.security import get_current_user_vulnerable, hash_ctf_flag
from app.schemas.ctf_schemas import CtfSubmitRequest
from app.db.repository import CtfRepository, GameSessionRepository
from app.services.siem_logger import log_security_event

router = APIRouter()

@router.post("/submit", tags=["Red Team - CTF"])
async def submit_flag(
    request: CtfSubmitRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user_vulnerable)
):
    """
    Endpoint for Red Team to submit captured flags.
    No UI for this, designed to be used via cURL/Postman or CLI.
    """
    user_id = user.get("id")
    lab_instance_id = user.get("lab_instance_id")

    if not user_id or not lab_instance_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")

    game_session = await GameSessionRepository.get_by_lab_instance_id(db, lab_instance_id)

    if not game_session:
        raise HTTPException(status_code=404, detail="Game session not found for this lab instance.")

    team_id = game_session.team_id

    hashed_submission = hash_ctf_flag(request.flag_value)

    flag = await CtfRepository.get_flag_by_value(db, hashed_submission)

    if not flag:
        await log_security_event(
            db=db,
            lab_instance_id=lab_instance_id,
            event_type="CTF_SUBMIT_FAILED",
            payload={"attempted_flag": request.flag_value},
            source_ip="CTF_ENGINE"
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
        source_ip="CTF_ENGINE"
    )

    return {
        "success": True,
        "message": f"Flag captured successfully! You earned {flag.reward} points.",
        "reward": flag.reward
    }