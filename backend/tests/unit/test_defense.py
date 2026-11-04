from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import GameSession
from app.labs.repository import GameSessionRepository
from app.services import defense


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
