from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class InvestigateRequest(BaseModel):
    log_id: UUID
    is_malicious: bool
    session_id: UUID


class InvestigateResponse(BaseModel):
    success: bool
    message: str
    credits_awarded: int
    new_balance: int


class AuditLogEntry(BaseModel):
    """Schema for returning individual SIEM logs to the frontend."""

    id: UUID
    event_type: str
    payload: Dict[str, Any]
    timestamp: datetime
    is_compromised: bool
    investigated_at: Optional[datetime] = None
    investigated_by: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)  # Allows reading from SQLAlchemy models


class DashboardStatsResponse(BaseModel):
    """Schema for aggregated dashboard metrics."""

    budget: int
    active_defenses: Dict[str, bool]
    total_logs: int
    compromised_logs: int
    pending_investigations: int
