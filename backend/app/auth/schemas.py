from uuid import UUID

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str
    # The lab the account belongs to; usernames are looked up only inside it.
    lab_id: UUID


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
