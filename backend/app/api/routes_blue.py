from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.database import get_db
from app.db.models import User
from app.schemas.blue_team import LoginRequest, TokenResponse, InvestigateRequest, InvestigateResponse
from app.core.security import verify_password, create_access_token, get_current_user_secure
from app.services.forensics import process_investigation

router = APIRouter()

@router.post("/login", response_model=TokenResponse, tags=["Blue Team"])
async def login_blue_team(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticates a Blue Team user and returns a signed JWT.
    """
    result = await db.execute(select(User).where(User.username == login_data.username))
    user = result.scalars().first()

    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    if user.role not in ["blue_team", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to access Blue Team panel")

    access_token = create_access_token(
        data={"sub": user.username, "role": user.role},
        expires_delta=timedelta(minutes=60)
    )

    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", tags=["Blue Team"])
async def get_my_profile(current_user: User = Depends(get_current_user_secure)):
    """A protected endpoint for validation."""
    return {
        "username": current_user.username,
        "role": current_user.role,
        "message": "Welcome to the secure Blue Team zone."
    }

@router.post("/forensics/investigate", response_model=InvestigateResponse, tags=["Blue Team"])
async def investigate_log(
    request_data: InvestigateRequest,
    current_user: User = Depends(get_current_user_secure),
    db: AsyncSession = Depends(get_db)
):
    """
    Allows Blue Team to investigate a security log and earn defense points.
    Delegates the validation and ML heuristics to the forensics service layer.
    """
    return await process_investigation(
        db=db,
        log_id=request_data.log_id,
        is_malicious_claim=request_data.is_malicious,
        session_id=request_data.session_id,
        user_id=current_user.id
    )