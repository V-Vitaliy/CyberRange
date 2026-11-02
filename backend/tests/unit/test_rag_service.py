import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from app.core.queue_manager import LLMQueueManager
from app.db.models import GameSession
from app.db.repository import ChatRepository
from app.services import rag_service


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


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_reranker_switch_and_system_prompt_reach_generation(monkeypatch, enabled):
    session = SimpleNamespace(lab_instance_id=uuid4(), use_reranker=enabled, system_prompt='Purchased custom instruction')
    monkeypatch.setattr(rag_service, 'get_authorized_session', AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, 'get_or_create_thread', AsyncMock())
    monkeypatch.setattr(ChatRepository, 'append_messages', AsyncMock())
    reranker = Mock()
    reranker.predict.return_value = [-1, 2]
    monkeypatch.setattr(rag_service, 'get_reranker', Mock(return_value=reranker))

    async def in_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(rag_service.asyncio, 'to_thread', in_thread)
    captured = {}

    async def stream(**kwargs):
        captured.update(kwargs)
        yield 'answer'

    monkeypatch.setattr(rag_service, 'generate_unified_stream', stream)
    store = SimpleNamespace(search_documents=Mock(return_value={'documents': [['discarded context', 'retained context']]}))
    user = dict(id=str(uuid4()), sub='student', role='red_team', lab_instance_id=str(session.lab_instance_id))
    parts = [part async for part in rag_service.generate_chat_response_sse(object(), uuid4(), uuid4(), 'question', LLMQueueManager(), store, user, object())]
    assert parts[-1] == 'data: [DONE]\n\n'
    assert 'Purchased custom instruction' in captured['system_prompt']
    assert 'retained context' in captured['human_prompt']
    assert ('discarded context' in captured['human_prompt']) is not enabled
    assert reranker.predict.called is enabled


@pytest.mark.asyncio
async def test_chat_sse_records_simulated_tool_result_and_history(harness, monkeypatch):
    from app.services import rag_service
    from app.db.repository import ChatRepository
    from app.core.queue_manager import LLMQueueManager

    audit, delivery = harness
    lab_id = uuid4()
    session = SimpleNamespace(lab_instance_id=lab_id, use_reranker=False, system_prompt='test')
    monkeypatch.setattr(rag_service, 'get_authorized_session', AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, 'get_or_create_thread', AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, 'append_messages', history)

    async def stream(messages):
        yield AIMessageChunk(content='', tool_call_chunks=[{
            'name': 'send_email', 'id': 'call1', 'index': 0,
            'args': json.dumps(dict(to_address='test@example.invalid', subject='Test', body='test')),
        }])

    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    user = dict(id=str(uuid4()), sub='student', role='red_team', lab_instance_id=str(lab_id))
    store = SimpleNamespace(search_documents=Mock(return_value={'documents': [[]]}))
    parts = [part async for part in rag_service.generate_chat_response_sse(object(), uuid4(), uuid4(), 'Send a test email', LLMQueueManager(), store, user, model)]
    assert parts[-1] == 'data: [DONE]\n\n'
    assert 'No email was sent' in history.call_args.kwargs['ai_response']
    assert audit.call_args.kwargs['lab_instance_id'] == str(lab_id)
    delivery.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_reason', ['stop', 'length'])
async def test_empty_chat_emits_sse_error_without_saving_success(harness, monkeypatch, finish_reason):
    from app.services import rag_service
    from app.db.repository import ChatRepository
    from app.core.queue_manager import LLMQueueManager

    lab_id = uuid4()
    session = SimpleNamespace(lab_instance_id=lab_id, use_reranker=False, system_prompt='test')
    monkeypatch.setattr(rag_service, 'get_authorized_session', AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, 'get_or_create_thread', AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, 'append_messages', history)

    async def stream(messages):
        yield AIMessageChunk(content='', response_metadata={'finish_reason': finish_reason})

    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    user = dict(id=str(uuid4()), sub='student', role='red_team', lab_instance_id=str(lab_id))
    store = SimpleNamespace(search_documents=Mock(return_value={'documents': [[]]}))
    parts = [part async for part in rag_service.generate_chat_response_sse(
        object(), uuid4(), uuid4(), 'Question', LLMQueueManager(), store, user, model)]
    assert parts[-1] == 'data: [DONE]\n\n'
    assert 'error' in json.loads(parts[0][6:])
    history.assert_not_awaited()
    harness[1].assert_not_called()
