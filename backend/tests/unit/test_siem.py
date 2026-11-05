from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.siem import service
from app.siem.forensics import analyzer
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


class FakeRedis:
    """SET NX EX semantics without a server; the TTL is recorded, not enforced."""

    def __init__(self, fail=False):
        self.keys, self.ttls, self.fail = set(), [], fail

    async def set(self, key, value, nx=False, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        self.ttls.append(ex)
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True


def recording_db():
    added = []
    db = SimpleNamespace(add=added.append, commit=AsyncMock(), refresh=AsyncMock(), rollback=AsyncMock())
    return db, added


async def forgery(db, lab, user, role="admin", blocked=False):
    await service.log_jwt_manipulation_attempt(db, lab, role, "203.0.113.9", user_id=user, blocked=blocked)


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed,malicious", [("admin", True), ("blue_team", True), ("red_team", False)])
async def test_jwt_forgery_event_is_judged_by_the_forensics_rule(monkeypatch, claimed, malicious):
    monkeypatch.setattr(service, "redis_client", FakeRedis(), raising=False)
    db, added = recording_db()
    lab = str(uuid4())
    await forgery(db, lab, str(uuid4()), claimed, blocked=True)
    [event] = added
    assert (event.event_type, event.lab_instance_id) == ("AUTH_BYPASS_ATTEMPT", lab)
    assert event.payload["claimed_role"] == claimed
    assert event.payload["source_ip"] == "203.0.113.9"
    assert event.payload["blocked"] is True
    assert analyzer.predict_is_malicious(event.payload, event.event_type) is malicious


@pytest.mark.asyncio
async def test_repeated_forgery_is_logged_once_per_window(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(service, "redis_client", redis, raising=False)
    db, added = recording_db()
    lab, user = str(uuid4()), str(uuid4())
    for _ in range(3):
        await forgery(db, lab, user)
    assert len(added) == 1
    assert set(redis.ttls) == {service.JWT_FORGERY_LOG_WINDOW_SECONDS}


@pytest.mark.asyncio
async def test_new_role_user_or_blocking_starts_a_new_event(monkeypatch):
    monkeypatch.setattr(service, "redis_client", FakeRedis(), raising=False)
    db, added = recording_db()
    lab, user = str(uuid4()), str(uuid4())
    await forgery(db, lab, user)
    await forgery(db, lab, user, role="blue_team")
    await forgery(db, lab, str(uuid4()))
    await forgery(db, lab, user, blocked=True)  # Strict Mode bought: the first rejection is visible.
    assert [(e.payload["claimed_role"], e.payload["blocked"]) for e in added] == [
        ("admin", False),
        ("blue_team", False),
        ("admin", False),
        ("admin", True),
    ]


@pytest.mark.asyncio
async def test_forgery_is_still_logged_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(service, "redis_client", FakeRedis(fail=True), raising=False)
    db, added = recording_db()
    lab, user = str(uuid4()), str(uuid4())
    await forgery(db, lab, user)
    await forgery(db, lab, user)
    assert len(added) == 2
