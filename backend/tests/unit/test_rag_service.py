import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from app.chat import service as rag_service
from app.chat import sqli_search
from app.chat.repository import ChatRepository
from app.core.queue_manager import LLMQueueManager
from app.db.models import GameSession


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
    result = [
        part
        async for part in rag_service.generate_chat_response_sse(
            object(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
        )
    ]
    where = store.search_documents.call_args.kwargs["where"]
    lab_filter = {"lab_instance_id": str(lab_id)}
    assert where == (lab_filter if role == "admin" else {"$and": [lab_filter, {"access_level": "public"}]})
    assert result[-1] == "data: [DONE]\n\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_reranker_switch_and_system_prompt_reach_generation(monkeypatch, enabled):
    session = SimpleNamespace(
        lab_instance_id=uuid4(), use_reranker=enabled, system_prompt="Purchased custom instruction"
    )
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    monkeypatch.setattr(ChatRepository, "append_messages", AsyncMock())
    reranker = Mock()
    reranker.predict.return_value = [-1, 2]
    monkeypatch.setattr(rag_service, "get_reranker", Mock(return_value=reranker))

    async def in_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(rag_service.asyncio, "to_thread", in_thread)
    captured = {}

    async def stream(**kwargs):
        captured.update(kwargs)
        yield "answer"

    monkeypatch.setattr(rag_service, "generate_unified_stream", stream)
    store = SimpleNamespace(
        search_documents=Mock(return_value={"documents": [["discarded context", "retained context"]]})
    )
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(session.lab_instance_id))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            object(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    assert "Purchased custom instruction" in captured["system_prompt"]
    assert "retained context" in captured["human_prompt"]
    assert ("discarded context" in captured["human_prompt"]) is not enabled
    assert reranker.predict.called is enabled


@pytest.mark.asyncio
async def test_chat_sse_records_simulated_tool_result_and_history(harness, monkeypatch):
    from app.chat import service as rag_service
    from app.chat.repository import ChatRepository
    from app.core.queue_manager import LLMQueueManager

    audit, delivery = harness
    lab_id = uuid4()
    session = SimpleNamespace(lab_instance_id=lab_id, use_reranker=False, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, "append_messages", history)

    async def stream(messages):
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": "send_email",
                    "id": "call1",
                    "index": 0,
                    "args": json.dumps(dict(to_address="test@example.invalid", subject="Test", body="test")),
                }
            ],
        )

    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(lab_id))
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            object(), uuid4(), uuid4(), "Send a test email", LLMQueueManager(), store, user, model
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    assert "No email was sent" in history.call_args.kwargs["ai_response"]
    assert audit.call_args.kwargs["lab_instance_id"] == str(lab_id)
    delivery.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
async def test_empty_chat_emits_sse_error_without_saving_success(harness, monkeypatch, finish_reason):
    from app.chat import service as rag_service
    from app.chat.repository import ChatRepository
    from app.core.queue_manager import LLMQueueManager

    lab_id = uuid4()
    session = SimpleNamespace(lab_instance_id=lab_id, use_reranker=False, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, "append_messages", history)

    async def stream(messages):
        yield AIMessageChunk(content="", response_metadata={"finish_reason": finish_reason})

    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(lab_id))
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            object(), uuid4(), uuid4(), "Question", LLMQueueManager(), store, user, model
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    assert "error" in json.loads(parts[0][6:])
    history.assert_not_awaited()
    harness[1].assert_not_called()


@pytest.mark.asyncio
async def test_chat_failure_hides_internal_details_from_client(monkeypatch, caplog):
    internal = "connect to postgresql://cyberadmin:hunter2@10.0.0.5:5432 failed"
    session = SimpleNamespace(lab_instance_id=uuid4(), use_reranker=False, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, "append_messages", history)

    async def stream(**kwargs):
        raise RuntimeError(internal)
        yield

    monkeypatch.setattr(rag_service, "generate_unified_stream", stream)
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(session.lab_instance_id))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            object(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    assert json.loads(parts[0][6:]) == {"error": rag_service.CHAT_ERROR_MESSAGE}
    assert not any(secret in "".join(parts) for secret in ("hunter2", "10.0.0.5", "postgresql"))
    assert any(internal in record.getMessage() + str(record.exc_info) for record in caplog.records)
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_search_runs_off_the_event_loop(monkeypatch):
    session = SimpleNamespace(lab_instance_id=uuid4(), use_reranker=False, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    monkeypatch.setattr(ChatRepository, "append_messages", AsyncMock())

    async def stream(**kwargs):
        yield "answer"

    monkeypatch.setattr(rag_service, "generate_unified_stream", stream)
    threads = []

    def search(*args, **kwargs):
        threads.append(threading.get_ident())
        assert not threading.current_thread().name.startswith("etl")
        return {"documents": [[]]}

    store = SimpleNamespace(search_documents=Mock(side_effect=search))
    user = dict(id=str(uuid4()), sub="student", role="admin", lab_instance_id=str(session.lab_instance_id))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            object(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    assert threads and threads[0] != threading.get_ident()
    assert store.search_documents.call_args.kwargs["where"] == {"lab_instance_id": str(session.lab_instance_id)}


@pytest.mark.asyncio
async def test_reranker_loading_and_scoring_run_off_the_event_loop(monkeypatch):
    session = SimpleNamespace(lab_instance_id=uuid4(), use_reranker=True, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    monkeypatch.setattr(ChatRepository, "append_messages", AsyncMock())

    async def stream(**kwargs):
        yield "answer"

    monkeypatch.setattr(rag_service, "generate_unified_stream", stream)
    threads = {}

    def predict(pairs):
        threads["predict"] = threading.get_ident()
        return [1.0 for _ in pairs]

    def load():
        threads["load"] = threading.get_ident()
        return SimpleNamespace(predict=predict)

    monkeypatch.setattr(rag_service, "get_reranker", load)
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [["context"]]}))
    user = dict(id=str(uuid4()), sub="student", role="admin", lab_instance_id=str(session.lab_instance_id))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            object(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    assert threading.get_ident() not in (threads.get("load"), threads.get("predict"))
    assert set(threads) == {"load", "predict"}


@pytest.mark.asyncio
async def test_history_search_goes_to_the_isolated_lab_reader(monkeypatch):
    found = ([{"title": "t"}], "SELECT ...")
    search = AsyncMock(return_value=found)
    monkeypatch.setattr(sqli_search, "search_history", search)
    assert await rag_service.search_chat_history("lab", "session", "query") is found
    search.assert_awaited_once_with("lab", "session", "query")


@pytest.mark.asyncio
async def test_thread_lookup_checks_owner_and_session(monkeypatch):
    thread = object()
    lookup = AsyncMock(return_value=thread)
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", lookup)
    db = object()
    assert await rag_service.get_or_create_thread(db, "thread", "user", "session", "prompt") is thread
    lookup.assert_awaited_once_with(db, "thread", "user", "session", "prompt")
