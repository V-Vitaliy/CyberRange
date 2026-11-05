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
    )
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=session))
    db = SimpleNamespace(commit=AsyncMock())
    await defense.process_defense_purchase(db, session.id, kind)
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
