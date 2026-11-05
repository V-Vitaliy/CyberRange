"""Red and Blue Team login; each router is mounted into its team's sub-application."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import LoginRequest, TokenResponse
from app.auth.service import authenticate, create_access_token, get_blue_team_user
from app.db.database import get_db
from app.db.models import User
from app.labs.service import get_session_for_lab
from app.siem.service import log_security_event

red_router = APIRouter()
blue_router = APIRouter()


@red_router.post("/login", tags=["Red Team Auth"])
async def red_team_login(login_req: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate(db, login_req.username, login_req.password)

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if user.role != "red_team":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access restricted")

    token_data = {
        "sub": user.username,
        "role": user.role,
        "id": str(user.id),
        "lab_instance_id": str(user.lab_instance_id),
    }

    token = create_access_token(data=token_data)

    # Log successful login
    await log_security_event(
        db=db,
        lab_instance_id=str(user.lab_instance_id),
        event_type="RED_TEAM_LOGIN",
        payload={"username": user.username, "status": "success"},
    )

    return {"access_token": token, "token_type": "bearer", "role": user.role}


@blue_router.post("/login", response_model=TokenResponse, tags=["Blue Team - User"])
async def login_blue_team(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticates a Blue Team user and returns a signed JWT."""
    user = await authenticate(db, login_data.username, login_data.password)

    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    if user.role not in ["blue_team", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to access Blue Team panel")

    token_data = {
        "sub": user.username,
        "id": str(user.id),
        "role": user.role,
        "lab_instance_id": str(user.lab_instance_id),
    }

    access_token = create_access_token(data=token_data, expires_delta=timedelta(minutes=60))

    return {"access_token": access_token, "token_type": "bearer"}


@blue_router.get("/me", tags=["Blue Team - User"])
async def get_my_profile(
    current_user: User = Depends(get_blue_team_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the authenticated profile and its laboratory's current session."""
    session = await get_session_for_lab(db, current_user.lab_instance_id)
    return {
        "username": current_user.username,
        "role": current_user.role,
        "session_id": str(session.id) if session else None,
        "message": "Welcome to the secure Blue Team zone.",
    }
