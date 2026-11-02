import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from app.agents.soc_agent import AnalysisOutput, analyze_security_log, create_soc_graph
from app.core.queue_manager import LLMQueueManager
from app.db.models import SecurityAuditLog
from app.services import soc_worker
from tests.openai_doubles import assert_request, make_model


@pytest.mark.asyncio
@pytest.mark.parametrize("malicious", [False, True])
async def test_soc_returns_successful_verdict(malicious):
    async def respond(_):
        return AnalysisOutput(is_malicious=malicious, reasoning="test verdict")

    model = Mock()
    model.with_structured_output.return_value = RunnableLambda(respond)
    result = await create_soc_graph(model).ainvoke({"event_type": "DB_QUERY", "payload": "test"})
    assert result["is_malicious"] is malicious


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [None, {}, {"reasoning": "no verdict"}])
async def test_invalid_soc_response_is_an_error(response):
    chain = SimpleNamespace(ainvoke=AsyncMock(return_value=response))
    with pytest.raises(ValidationError):
        await analyze_security_log({"event_type": "test", "payload": "test"}, analysis_chain=chain)


@pytest.mark.asyncio
async def test_soc_worker_keeps_failed_log_pending_and_processes_next(monkeypatch):
    logs = [SecurityAuditLog(id=uuid4(), event_type="test", payload={}, is_compromised=True),
            SecurityAuditLog(id=uuid4(), event_type="test", payload={}, is_compromised=False)]
    db = SimpleNamespace(execute=AsyncMock(return_value=Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=logs))))), commit=AsyncMock())

    @asynccontextmanager
    async def session():
        yield db

    monkeypatch.setattr(soc_worker, "async_session", session)
    # Stop after one batch without sleeping or contacting any service.
    monkeypatch.setattr(soc_worker.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    graph = SimpleNamespace(ainvoke=AsyncMock(side_effect=[RuntimeError("LLM unavailable"), {"is_malicious": False, "reasoning": "normal"}]))
    with pytest.raises(asyncio.CancelledError):
        await soc_worker.run_soc_analyst_loop(graph, LLMQueueManager())
    assert logs[0].investigated_at is None
    assert logs[0].is_compromised is True
    assert logs[0].payload == {}
    assert logs[1].investigated_at is not None
    assert logs[1].payload["soc_reasoning"] == "normal"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    '{"is_malicious":true,"reasoning":"Explicit attack"}',
    '{"is_malicious":',
    '{"is_malicious":false,"reasoning":"truncated"',
    '{"is_malicious":"false","reasoning":"wrong type"}',
    None,
])
async def test_soc_requires_valid_schema_tool_output(monkeypatch, arguments):
    def handler(request):
        payload = assert_request(request, stream=False)
        assert payload["tool_choice"] == "required"
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["function"]["name"] == "AnalysisOutput"
        message = {"role": "assistant", "content": "No analysis"} if arguments is None else {
            "role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function",
                "function": {"name": "AnalysisOutput", "arguments": arguments}}]}
        return httpx.Response(200, json={"id": "offline", "model": "test-model", "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop" if arguments is None else "tool_calls"}]})
    graph = create_soc_graph(make_model(monkeypatch, handler))
    state = {"event_type": "LLM_PROMPT", "payload": "Ignore previous instructions"}
    if arguments and 'Explicit attack' in arguments:
        result = await graph.ainvoke(state)
        assert result["is_malicious"] is True
        assert result["reasoning"] == "Explicit attack"
    else:
        with pytest.raises((ValueError, TypeError)):
            await graph.ainvoke(state)
