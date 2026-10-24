from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.queue_manager import LLMQueueManager
from app.db.repository import ChatRepository
from app.services import rate_limiter, rag_service


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_rate_limit_only_blocks_when_purchased(monkeypatch, enabled):
    session = SimpleNamespace(rate_limit_enabled=enabled, rate_limit_rpm=2, lab_instance_id=uuid4())
    monkeypatch.setattr(rate_limiter.GameSessionRepository, 'get_by_id', AsyncMock(return_value=session))
    audit = AsyncMock()
    monkeypatch.setattr(rate_limiter, 'log_security_event', audit)
    redis = AsyncMock()
    redis.incr.side_effect = [1, 2, 3]
    session_id = uuid4()
    for _ in range(2):
        await rate_limiter.enforce_rate_limit(object(), redis, session_id, '127.0.0.1')
    if enabled:
        with pytest.raises(HTTPException) as exc:
            await rate_limiter.enforce_rate_limit(object(), redis, session_id, '127.0.0.1')
        assert exc.value.status_code == 429
        redis.expire.assert_awaited_once()
        assert audit.call_args.kwargs['event_type'] == 'DOS_ATTEMPT_BLOCKED'
    else:
        await rate_limiter.enforce_rate_limit(object(), redis, session_id, '127.0.0.1')
        redis.incr.assert_not_awaited()
        audit.assert_not_awaited()


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
