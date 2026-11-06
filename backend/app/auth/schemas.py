from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    # Signed code of the lab the account belongs to (from admin_setup); users are looked up only inside it.
    lab_code: str = Field(max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
