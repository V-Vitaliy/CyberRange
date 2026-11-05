from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_blue_team_user
from app.db.database import get_db
from app.db.models import User
from app.defense.schemas import BuyDefenseRequest, BuyDefenseResponse
from app.defense.service import process_defense_purchase
from app.labs.service import get_authorized_session

router = APIRouter()


@router.post("/defenses/buy", response_model=BuyDefenseResponse, tags=["Blue Team - Shop"])
async def buy_defense(
    request_data: BuyDefenseRequest,
    current_user: User = Depends(get_blue_team_user),
    db: AsyncSession = Depends(get_db),
):
    """Allows Blue Team to spend their defense budget on security patches."""
    await get_authorized_session(db, request_data.session_id, current_user.lab_instance_id)
    return await process_defense_purchase(
        db=db, session_id=request_data.session_id, defense_type=request_data.defense_type
    )
