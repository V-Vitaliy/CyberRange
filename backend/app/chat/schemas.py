import uuid

from pydantic import BaseModel, Field

from app.core.config import settings


class ChatRequest(BaseModel):
    """
    Schema for incoming chat messages from the Red Team.
    """

    prompt: str = Field(min_length=1, max_length=settings.PROMPT_MAX_CHARS)
    session_id: str
    thread_id: uuid.UUID
