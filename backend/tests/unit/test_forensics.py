from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import GameSession, SecurityAuditLog
from app.labs.repository import GameSessionRepository
from app.services import forensics


@pytest.mark.asyncio
async def test_investigation_cannot_reward_foreign_log(monkeypatch):
    log = SecurityAuditLog(id=uuid4(), lab_instance_id=uuid4(), payload={}, event_type="test", is_compromised=False)
    session = GameSession(id=uuid4(), lab_instance_id=uuid4(), defense_budget=5)
    monkeypatch.setattr(forensics.AuditLogRepository, "get_by_id", AsyncMock(return_value=log))
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=session))
    db = SimpleNamespace(commit=AsyncMock())
    with pytest.raises(HTTPException):
        await forensics.process_investigation(db, log.id, False, session.id, uuid4())
    assert log.investigated_at is None
    assert session.defense_budget == 5
    db.commit.assert_not_awaited()
