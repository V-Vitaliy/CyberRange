from pydantic import BaseModel
from uuid import UUID

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class InvestigateRequest(BaseModel):
    log_id: UUID
    is_malicious: bool
    session_id: UUID

class InvestigateResponse(BaseModel):
    success: bool
    message: str
    credits_awarded: int
    new_balance: int