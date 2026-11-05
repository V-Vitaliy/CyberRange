from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_blue_team_user
from app.db.database import get_db
from app.db.models import User
from app.labs.service import get_authorized_session
from app.siem import service
from app.siem.forensics import process_investigation
from app.siem.schemas import AuditLogEntry, DashboardStatsResponse, InvestigateRequest, InvestigateResponse

router = APIRouter()


@router.post("/investigate", response_model=InvestigateResponse, tags=["Blue Team - Forensics"])
async def investigate_log(
    req: InvestigateRequest, current_user: User = Depends(get_blue_team_user), db: AsyncSession = Depends(get_db)
):
    """Processes a log investigation and awards points if the verdict is correct."""
    await get_authorized_session(db, req.session_id, current_user.lab_instance_id)
    return await process_investigation(
        db=db,
        log_id=req.log_id,
        is_malicious_claim=req.is_malicious,
        session_id=req.session_id,
        user_id=current_user.id,
    )


@router.get("/dashboard", response_model=DashboardStatsResponse, tags=["Blue Team - Dashboard"])
async def get_dashboard_stats(
    session_id: str = Query(..., description="Current game session ID"),
    current_user: User = Depends(get_blue_team_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregates metrics for the Blue Team React Dashboard (KPI cards)."""
    game_session = await get_authorized_session(db, session_id, current_user.lab_instance_id)

    if not game_session:
        raise HTTPException(status_code=404, detail="Game session not found.")

    return await service.get_dashboard_stats(db, game_session)


@router.get("/logs", response_model=List[AuditLogEntry], tags=["Blue Team - SIEM"])
async def get_audit_logs(
    session_id: str = Query(..., description="Current game session ID"),
    limit: int = Query(50, ge=1, le=500, description="Max number of logs to fetch"),
    current_user: User = Depends(get_blue_team_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetches raw SIEM logs for the investigation table."""
    game_session = await get_authorized_session(db, session_id, current_user.lab_instance_id)
    if not game_session:
        raise HTTPException(status_code=404, detail="Game session not found.")

    return await service.get_logs(db, game_session.lab_instance_id, limit)
