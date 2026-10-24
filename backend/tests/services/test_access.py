from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import ChatThread, GameSession, SecurityAuditLog
from app.db.repository import ChatRepository
from app.services import defense, forensics, rag_service
from app.core.queue_manager import LLMQueueManager


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_user,foreign_session", [(True, False), (False, True)])
async def test_existing_thread_requires_owner_and_session(foreign_user, foreign_session):
    user_id, session_id = uuid4(), uuid4()
    thread = ChatThread(id=uuid4(), user_id=uuid4() if foreign_user else user_id,
                        session_id=uuid4() if foreign_session else session_id)
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock(scalars=Mock(return_value=Mock(first=Mock(return_value=thread))))), commit=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await ChatRepository.get_or_create_thread(db, thread.id, user_id, session_id, "hello")
    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_investigation_cannot_reward_foreign_log(monkeypatch):
    log = SecurityAuditLog(id=uuid4(), lab_instance_id=uuid4(), payload={}, event_type="test", is_compromised=False)
    session = GameSession(id=uuid4(), lab_instance_id=uuid4(), defense_budget=5)
    monkeypatch.setattr(forensics.AuditLogRepository, "get_by_id", AsyncMock(return_value=log))
    monkeypatch.setattr(forensics.GameSessionRepository, "get_by_id", AsyncMock(return_value=session))
    db = SimpleNamespace(commit=AsyncMock())
    with pytest.raises(HTTPException):
        await forensics.process_investigation(db, log.id, False, session.id, uuid4())
    assert log.investigated_at is None
    assert session.defense_budget == 5
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,attribute", [("jwt_filter", "jwt_filter_enabled"), ("rate_limit", "rate_limit_enabled"), ("reranker", "use_reranker"), ("system_prompt", "system_prompt")])
async def test_purchase_activates_once_and_charges_correct_price(monkeypatch, kind, attribute):
    session = GameSession(id=uuid4(), defense_budget=5, system_prompt="You are a helpful university assistant.",
                          jwt_filter_enabled=False, rate_limit_enabled=False, use_reranker=False)
    monkeypatch.setattr(defense.GameSessionRepository, "get_by_id", AsyncMock(return_value=session))
    db = SimpleNamespace(commit=AsyncMock())
    await defense.process_defense_purchase(db, session.id, kind)
    assert session.defense_budget == 5 - defense.DEFENSE_PRICING[kind]
    assert getattr(session, attribute)
    with pytest.raises(HTTPException):
        await defense.process_defense_purchase(db, session.id, kind)
    assert session.defense_budget == 5 - defense.DEFENSE_PRICING[kind]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["red_team", "admin"])
async def test_rag_limits_both_roles_to_their_lab(monkeypatch, role):
    lab_id = uuid4()
    session = GameSession(lab_instance_id=lab_id, use_reranker=False, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    monkeypatch.setattr(ChatRepository, "append_messages", AsyncMock())

    async def stream(**kwargs):
        yield "test response"

    monkeypatch.setattr(rag_service, "generate_unified_stream", stream)
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    user = dict(id=str(uuid4()), sub="student", role=role, lab_instance_id=str(lab_id))
    result = [part async for part in rag_service.generate_chat_response_sse(
        object(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object())]
    where = store.search_documents.call_args.kwargs["where"]
    lab_filter = {"lab_instance_id": str(lab_id)}
    assert where == (lab_filter if role == "admin" else {"$and": [lab_filter, {"access_level": "public"}]})
    assert result[-1] == "data: [DONE]\n\n"
