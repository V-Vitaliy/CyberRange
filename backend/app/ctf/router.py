from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_current_user_vulnerable
from app.ctf.schemas import CtfSubmitRequest
from app.ctf.service import submit_flag as submit_ctf_flag
from app.db.database import get_db

router = APIRouter()


@router.post("/submit", tags=["Red Team - CTF"])
async def submit_flag(
    request: CtfSubmitRequest, db: AsyncSession = Depends(get_db), user: dict = Depends(get_current_user_vulnerable)
):
    """
    Endpoint for Red Team to submit captured flags.
    No UI for this, designed to be used via cURL/Postman or CLI.
    """
    return await submit_ctf_flag(db, user, request.flag_value)
