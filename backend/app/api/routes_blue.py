from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.db.database import get_db
from app.core.security import verify_password, create_access_token, get_current_user_secure
from app.services.forensics import process_investigation
from app.services.defense import process_defense_purchase
from app.db.models import User
from app.schemas.blue_team import (
    LoginRequest, TokenResponse,
    BuyDefenseRequest, BuyDefenseResponse,
    InvestigateRequest, InvestigateResponse,
    DashboardStatsResponse, AuditLogEntry
)
from app.db.repository import (
    UserRepository,
    GameSessionRepository,
    AuditLogRepository
)

router = APIRouter()

@router.post("/login", response_model=TokenResponse, tags=["Blue Team - User"])
async def login_blue_team(login_data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticates a Blue Team user and returns a signed JWT."""
    user = await UserRepository.get_by_username(db, login_data.username)

    if not user or not verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect username or password")

    if user.role not in ["blue_team", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to access Blue Team panel")

    token_data = {
        "sub": user.username,
        "id": str(user.id),
        "role": user.role,
        "lab_instance_id": str(user.lab_instance_id)
    }

    access_token = create_access_token(
        data=token_data,
        expires_delta=timedelta(minutes=60)
    )

    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/me", tags=["Blue Team - User"])
async def get_my_profile(current_user: User = Depends(get_current_user_secure)):
    """A protected endpoint for validation."""
    return {
        "username": current_user.username,
        "role": current_user.role,
        "message": "Welcome to the secure Blue Team zone."
    }

@router.post("/defenses/buy", response_model=BuyDefenseResponse, tags=["Blue Team - Shop"])
async def buy_defense(
    request_data: BuyDefenseRequest,
    current_user: User = Depends(get_current_user_secure),
    db: AsyncSession = Depends(get_db)
):
    """Allows Blue Team to spend their defense budget on security patches."""
    return await process_defense_purchase(
        db=db,
        session_id=request_data.session_id,
        defense_type=request_data.defense_type
    )

@router.post("/investigate", response_model=InvestigateResponse, tags=["Blue Team - Forensics"])
async def investigate_log(
    req: InvestigateRequest,
    current_user: User = Depends(get_current_user_secure),
    db: AsyncSession = Depends(get_db)
):
    """Processes a log investigation and awards points if the verdict is correct."""
    return await process_investigation(
        db=db,
        log_id=req.log_id,
        is_malicious_claim=req.is_malicious,
        session_id=req.session_id,
        user_id=current_user.id
    )

@router.get("/dashboard", response_model=DashboardStatsResponse, tags=["Blue Team - Dashboard"])
async def get_dashboard_stats(
    session_id: str = Query(..., description="Current game session ID"),
    current_user: User = Depends(get_current_user_secure),
    db: AsyncSession = Depends(get_db)
):
    """Aggregates metrics for the Blue Team React Dashboard (KPI cards)."""
    game_session = await GameSessionRepository.get_by_id(db, session_id)

    if not game_session:
        raise HTTPException(status_code=404, detail="Game session not found.")

    active_defenses = {
        "prompt_hardening_enabled": game_session.system_prompt != "You are a helpful university assistant.",
        "rate_limit_enabled": game_session.rate_limit_enabled,
        "reranker_enabled": game_session.use_reranker,
        "jwt_filter_enabled": game_session.jwt_filter_enabled
    }

    stats = await AuditLogRepository.get_stats(db, game_session.lab_instance_id)

    return DashboardStatsResponse(
        budget=game_session.defense_budget,
        active_defenses=active_defenses,
        **stats
    )

@router.get("/logs", response_model=List[AuditLogEntry], tags=["Blue Team - SIEM"])
async def get_audit_logs(
    session_id: str = Query(..., description="Current game session ID"),
    limit: int = Query(50, description="Max number of logs to fetch"),
    current_user: User = Depends(get_current_user_secure),
    db: AsyncSession = Depends(get_db)
):
    """Fetches raw SIEM logs for the investigation table."""
    game_session = await GameSessionRepository.get_by_id(db, session_id)
    if not game_session:
        raise HTTPException(status_code=404, detail="Game session not found.")

    return await AuditLogRepository.get_logs(db, game_session.lab_instance_id, limit)