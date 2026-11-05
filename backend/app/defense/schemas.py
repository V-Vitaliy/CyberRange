from uuid import UUID

from pydantic import BaseModel


class BuyDefenseRequest(BaseModel):
    session_id: UUID
    defense_type: str  # e.g., "system_prompt", "rate_limit", "reranker", "jwt_filter"


class BuyDefenseResponse(BaseModel):
    success: bool
    message: str
    new_balance: int
    active_defenses: dict
