from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.siem import service
from app.siem.repository import AuditLogRepository

STATS = {"total_logs": 7, "compromised_logs": 2, "pending_investigations": 3}


def game_session(**defenses):
    values = dict(system_prompt="You are a helpful university assistant.", rate_limit_enabled=False)
    values.update(use_reranker=False, jwt_filter_enabled=False, defense_budget=5, lab_instance_id=uuid4())
    values.update(defenses)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_dashboard_combines_lab_stats_budget_and_defenses(monkeypatch):
    stats = AsyncMock(return_value=dict(STATS))
    monkeypatch.setattr(AuditLogRepository, "get_stats", stats)
    session = game_session(system_prompt="hardened", jwt_filter_enabled=True)
    db = object()
    result = await service.get_dashboard_stats(db, session)
    stats.assert_awaited_once_with(db, session.lab_instance_id)
    assert result.model_dump() == dict(
        budget=5,
        active_defenses=dict(
            prompt_hardening_enabled=True, rate_limit_enabled=False, reranker_enabled=False, jwt_filter_enabled=True
        ),
        **STATS,
    )


@pytest.mark.asyncio
async def test_logs_are_read_for_the_sessions_lab(monkeypatch):
    rows = [object()]
    logs = AsyncMock(return_value=rows)
    monkeypatch.setattr(AuditLogRepository, "get_logs", logs)
    session = game_session()
    db = object()
    assert await service.get_logs(db, session.lab_instance_id, 20) is rows
    logs.assert_awaited_once_with(db, session.lab_instance_id, 20)
