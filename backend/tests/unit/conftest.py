from unittest.mock import AsyncMock, Mock

import pytest

from app.agents.tools import email_tool, registry
from app.agents.tools.email_service import EmailService


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(email_tool.settings, "SMTP_HOST", "")
    audit = AsyncMock()
    monkeypatch.setattr(registry, "log_security_event", audit)
    delivery = Mock(return_value=True)
    monkeypatch.setattr(EmailService, "send_real_email", delivery)

    async def in_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(email_tool.asyncio, "to_thread", in_thread)
    return audit, delivery
