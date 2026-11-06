from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import GameSession
from app.defense import service as defense
from app.labs.repository import GameSessionRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,attribute",
    [
        ("jwt_filter", "jwt_filter_enabled"),
        ("rate_limit", "rate_limit_enabled"),
        ("reranker", "use_reranker"),
        ("system_prompt", "system_prompt"),
        ("login_hardening", "login_hardening_enabled"),
    ],
)
async def test_purchase_activates_once_and_charges_correct_price(monkeypatch, kind, attribute):
    session = GameSession(
        id=uuid4(),
        defense_budget=5,
        system_prompt="You are a helpful university assistant.",
        jwt_filter_enabled=False,
        rate_limit_enabled=False,
        use_reranker=False,
        login_hardening_enabled=False,
    )
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=session))
    db = SimpleNamespace(commit=AsyncMock())
    result = await defense.process_defense_purchase(db, session.id, kind)
    assert result.active_defenses["login_hardening_enabled"] is (kind == "login_hardening")
    assert session.defense_budget == 5 - defense.DEFENSE_PRICING[kind]
    assert getattr(session, attribute)
    with pytest.raises(HTTPException):
        await defense.process_defense_purchase(db, session.id, kind)
    assert session.defense_budget == 5 - defense.DEFENSE_PRICING[kind]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_rate_limit_only_blocks_when_purchased(monkeypatch, enabled):
    session = SimpleNamespace(rate_limit_enabled=enabled, rate_limit_rpm=2, lab_instance_id=uuid4())
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=session))
    audit = AsyncMock()
    monkeypatch.setattr(defense, "log_security_event", audit)
    redis = AsyncMock()
    redis.incr.side_effect = [1, 2, 3]
    session_id = uuid4()
    for _ in range(2):
        await defense.enforce_rate_limit(object(), redis, session_id, "127.0.0.1")
    if enabled:
        with pytest.raises(HTTPException) as exc:
            await defense.enforce_rate_limit(object(), redis, session_id, "127.0.0.1")
        assert exc.value.status_code == 429
        redis.expire.assert_awaited_once()
        assert audit.call_args.kwargs["event_type"] == "DOS_ATTEMPT_BLOCKED"
    else:
        await defense.enforce_rate_limit(object(), redis, session_id, "127.0.0.1")
        redis.incr.assert_not_awaited()
        audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_block_is_logged_not_printed(monkeypatch, capsys, caplog):
    session = SimpleNamespace(rate_limit_enabled=True, rate_limit_rpm=1, lab_instance_id=uuid4())
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=session))
    monkeypatch.setattr(defense, "log_security_event", AsyncMock())
    redis = AsyncMock()
    redis.incr.return_value = 2
    with caplog.at_level("DEBUG", logger=defense.logger.name):
        with pytest.raises(HTTPException):
            await defense.enforce_rate_limit(object(), redis, uuid4(), "203.0.113.7")
    assert capsys.readouterr().out == ""
    blocked = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(blocked) == 1
    assert "203.0.113.7" in blocked[0].getMessage()


class CountingRedis:
    """INCR and EXPIRE per key without a server; the TTL is recorded, not enforced."""

    def __init__(self, fail=False):
        self.counts, self.ttls, self.fail = {}, {}, fail

    async def incr(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        self.ttls[key] = seconds


def hardened_lab():
    return SimpleNamespace(lab_instance_id=uuid4(), login_hardening_enabled=True)


@pytest.fixture
def login_audit(monkeypatch):
    audit = AsyncMock()
    monkeypatch.setattr(defense, "log_security_event", audit)
    return audit


async def attempts(redis, lab, count, source_ip="203.0.113.9"):
    """Return how many of the attempts were blocked with 429."""
    blocked = 0
    for _ in range(count):
        try:
            await defense.enforce_login_attempt_limit(redis, lab, source_ip)
        except HTTPException as exc:
            assert exc.status_code == 429
            blocked += 1
    return blocked


@pytest.mark.asyncio
async def test_login_attempts_are_limited_per_lab_and_source(login_audit):
    redis, lab = CountingRedis(), hardened_lab()
    assert await attempts(redis, lab, defense.LOGIN_ATTEMPTS_PER_MINUTE) == 0
    assert await attempts(redis, lab, 1) == 1
    # Another source in the same lab and the same source in another lab keep their own budget.
    assert await attempts(redis, lab, 1, source_ip="198.51.100.7") == 0
    assert await attempts(redis, hardened_lab(), 1) == 0
    assert set(redis.ttls.values()) == {60}


@pytest.mark.asyncio
async def test_blocked_login_burst_is_logged_once_per_window(login_audit):
    redis, lab = CountingRedis(), hardened_lab()
    assert await attempts(redis, lab, defense.LOGIN_ATTEMPTS_PER_MINUTE + 3) == 3
    login_audit.assert_awaited_once()
    kwargs = login_audit.call_args.kwargs
    assert (kwargs["lab_instance_id"], kwargs["event_type"], kwargs["source_ip"]) == (
        str(lab.lab_instance_id),
        "LOGIN_RATE_LIMITED",
        "203.0.113.9",
    )
    assert kwargs["payload"]["limit"] == defense.LOGIN_ATTEMPTS_PER_MINUTE


@pytest.mark.asyncio
async def test_login_limit_lets_logins_through_when_redis_is_down(login_audit):
    assert await attempts(CountingRedis(fail=True), hardened_lab(), defense.LOGIN_ATTEMPTS_PER_MINUTE + 1) == 0
    login_audit.assert_not_awaited()
