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

    async def delete(self, key):
        self.keys.discard(key)


class AuditSessions:
    """Stand-in for async_session(): each event gets its own session; commits can be made to fail."""

    def __init__(self, commit_errors=()):
        self.added, self.opened, self.rollbacks = [], 0, 0
        self.commit_errors = list(commit_errors)

    def __call__(self):
        return self

    async def __aenter__(self):
        self.opened += 1
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        if self.commit_errors and self.commit_errors.pop(0):
            raise RuntimeError("database down")

    async def refresh(self, row):
        pass

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def audit(monkeypatch):
    sessions = AuditSessions()
    monkeypatch.setattr(service, "async_session", sessions, raising=False)
    monkeypatch.setattr(service, "redis_client", FakeRedis(), raising=False)
    return sessions


async def forgery(lab, user, role="admin", blocked=False):
    await service.log_jwt_manipulation_attempt(lab, role, "203.0.113.9", user_id=user, blocked=blocked)


@pytest.mark.asyncio
async def test_event_is_written_in_its_own_session(audit):
    lab = str(uuid4())
    event = await service.log_security_event(lab, "TEST_EVENT", {"k": "v"}, source_ip="203.0.113.9")
    assert audit.opened == 1
    assert audit.added == [event]
    assert (event.lab_instance_id, event.event_type, event.payload) == (
        lab,
        "TEST_EVENT",
        {"k": "v", "source_ip": "203.0.113.9"},
    )


@pytest.mark.asyncio
async def test_failed_write_rolls_back_only_its_own_session(audit):
    audit.commit_errors = [True]
    assert await service.log_security_event(str(uuid4()), "TEST_EVENT", {}) is None
    assert audit.rollbacks == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed,malicious", [("admin", True), ("blue_team", True), ("red_team", False)])
async def test_jwt_forgery_event_is_judged_by_the_forensics_rule(audit, claimed, malicious):
    lab = str(uuid4())
    await forgery(lab, str(uuid4()), claimed, blocked=True)
    [event] = audit.added
    assert (event.event_type, event.lab_instance_id) == ("AUTH_BYPASS_ATTEMPT", lab)
    assert event.payload["claimed_role"] == claimed
    assert event.payload["source_ip"] == "203.0.113.9"
    assert event.payload["blocked"] is True
    assert analyzer.predict_is_malicious(event.payload, event.event_type) is malicious


@pytest.mark.asyncio
async def test_repeated_forgery_is_logged_once_per_window(audit):
    lab, user = str(uuid4()), str(uuid4())
    for _ in range(3):
        await forgery(lab, user)
    assert len(audit.added) == 1
    assert set(service.redis_client.ttls) == {service.JWT_FORGERY_LOG_WINDOW_SECONDS}


@pytest.mark.asyncio
async def test_new_role_user_or_blocking_starts_a_new_event(audit):
    lab, user = str(uuid4()), str(uuid4())
    await forgery(lab, user)
    await forgery(lab, user, role="blue_team")
    await forgery(lab, str(uuid4()))
    await forgery(lab, user, blocked=True)  # Strict Mode bought: the first rejection is visible.
    assert [(e.payload["claimed_role"], e.payload["blocked"]) for e in audit.added] == [
        ("admin", False),
        ("blue_team", False),
        ("admin", False),
        ("admin", True),
    ]


@pytest.mark.asyncio
async def test_forgery_is_still_logged_when_redis_is_down(audit, monkeypatch):
    monkeypatch.setattr(service, "redis_client", FakeRedis(fail=True))
    lab, user = str(uuid4()), str(uuid4())
    await forgery(lab, user)
    await forgery(lab, user)
    assert len(audit.added) == 2


@pytest.mark.asyncio
async def test_failed_event_write_releases_the_window(audit):
    audit.commit_errors = [True, False]
    lab, user = str(uuid4()), str(uuid4())
    await forgery(lab, user)  # log_security_event rolls back and returns None.
    await forgery(lab, user)
    assert audit.opened == 2
    assert audit.commit_errors == []
