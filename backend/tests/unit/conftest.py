from unittest.mock import AsyncMock, Mock

import pytest

from app.core import llm_engine
from app.services.email_service import EmailService


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(llm_engine.settings, "SMTP_HOST", "")
    audit = AsyncMock()
    monkeypatch.setattr(llm_engine, "log_security_event", audit)
    delivery = Mock(return_value=True)
    monkeypatch.setattr(EmailService, "send_real_email", delivery)

    async def in_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(llm_engine.asyncio, "to_thread", in_thread)
    return audit, delivery
