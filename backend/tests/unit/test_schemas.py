from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.red_team import ChatRequest


@pytest.mark.parametrize("length", [0, settings.PROMPT_MAX_CHARS + 1])
def test_chat_prompt_length_is_bounded(length):
    with pytest.raises(ValidationError):
        ChatRequest(prompt="x" * length, session_id=str(uuid4()), thread_id=uuid4())


def test_chat_prompt_at_limit_is_accepted():
    request = ChatRequest(prompt="x" * settings.PROMPT_MAX_CHARS, session_id=str(uuid4()), thread_id=uuid4())
    assert len(request.prompt) == settings.PROMPT_MAX_CHARS
