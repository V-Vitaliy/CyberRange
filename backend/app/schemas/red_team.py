import uuid

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """
    Schema for incoming chat messages from the Red Team.
    """
    prompt: str
    session_id: str
    thread_id: uuid.UUID

class LoginRequest(BaseModel):
    username: str
    password: str