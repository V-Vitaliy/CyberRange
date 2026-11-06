from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import GameSession, SecurityAuditLog
from app.labs.repository import GameSessionRepository
from app.siem import forensics


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


@pytest.mark.parametrize("count,malicious", [(None, False), (1, False), (4, False), (5, True), (30, True)])
def test_failed_login_is_malicious_only_as_an_enumeration_burst(count, malicious):
    assert forensics.LOGIN_ENUMERATION_THRESHOLD == 5  # distinct usernames from one source in the window
    # SQL-like usernames do not turn a single failed login into an attack.
    payload = {"username": "' or 1=1 --", "failed_usernames_from_source": count}
    assert forensics.analyzer.predict_is_malicious(payload, "LOGIN_FAILED") is malicious


def test_blocked_login_burst_is_malicious():
    assert forensics.analyzer.predict_is_malicious({"limit": 10}, "LOGIN_RATE_LIMITED") is True
