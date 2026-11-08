import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk

from app.agents.tools.base import ToolContext
from app.chat import service as rag_service
from app.chat import sqli_search
from app.chat.prompt_builder import PromptBuilder
from app.chat.repository import ChatRepository
from app.core.queue_manager import LLMQueueManager
from app.db.models import GameSession


def request_db():
    """The request's session; the service ends its read transaction before the model runs."""
    return SimpleNamespace(rollback=AsyncMock())


@pytest.fixture(autouse=True)
def no_exercise_secret(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(rag_service, "get_exercise_secret", lookup, raising=False)
    return lookup


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["red_team", "admin"])
async def test_chat_passes_the_turn_context_without_searching_in_advance(monkeypatch, role):
    lab_id = uuid4()
    session = GameSession(lab_instance_id=lab_id, use_reranker=False, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    monkeypatch.setattr(ChatRepository, "append_messages", AsyncMock())
    captured = {}

    async def stream(**kwargs):
        captured.update(kwargs)
        yield {"text": "test response"}

    monkeypatch.setattr(rag_service, "run_agent_turn", stream)
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    user = dict(id=str(uuid4()), sub="student", role=role, lab_instance_id=str(lab_id))
    result = [
        part
        async for part in rag_service.generate_chat_response_sse(
            request_db(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
        )
    ]
    assert result[-1] == "data: [DONE]\n\n"
    # Documents are searched only when the model calls the tool.
    store.search_documents.assert_not_called()
    assert captured["context"] == ToolContext(
        lab_instance_id=str(lab_id), role=role, use_reranker=False, vector_store=store
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_reranker_flag_and_system_prompt_reach_the_agent_turn(monkeypatch, enabled):
    session = SimpleNamespace(
        lab_instance_id=uuid4(), use_reranker=enabled, system_prompt="Purchased custom instruction"
    )
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    monkeypatch.setattr(ChatRepository, "append_messages", AsyncMock())
    captured = {}

    async def stream(**kwargs):
        captured.update(kwargs)
        yield {"text": "answer"}

    monkeypatch.setattr(rag_service, "run_agent_turn", stream)
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [["context"]]}))
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(session.lab_instance_id))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            request_db(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    assert "Purchased custom instruction" in captured["system_prompt"]
    assert "question" in captured["human_prompt"]
    assert captured["context"].use_reranker is enabled
    store.search_documents.assert_not_called()


@pytest.mark.asyncio
async def test_chat_sse_streams_agent_steps_and_saves_the_final_answer(harness, monkeypatch):
    from app.chat import llm_engine
    from tests.unit.test_llm_engine import EMAIL, ScriptedModel, tool_chunk

    audit, delivery = harness
    monkeypatch.setattr(llm_engine, "log_security_event", AsyncMock(), raising=False)
    lab_id = uuid4()
    session = SimpleNamespace(lab_instance_id=lab_id, use_reranker=False, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, "append_messages", history)
    model = Mock()
    model.bind_tools.return_value = ScriptedModel([tool_chunk(EMAIL)], [AIMessageChunk(content="I tried.")])
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(lab_id))
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            request_db(), uuid4(), uuid4(), "Send a test email", LLMQueueManager(), store, user, model
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    messages = [json.loads(part[6:]) for part in parts[:-1]]
    [step] = [message["step"] for message in messages if "step" in message]
    assert (step["step"], step["tool"]) == (1, "send_email")
    assert "No email was sent" in step["outcome"]
    assert "".join(message.get("text", "") for message in messages) == "I tried."
    assert history.call_args.kwargs["ai_response"] == "I tried."
    assert audit.call_args.kwargs["lab_instance_id"] == str(lab_id)
    delivery.assert_called_once()


@pytest.mark.asyncio
async def test_request_transaction_ends_before_the_model_runs(monkeypatch):
    """A turn can take many seconds: holding the request's connection meanwhile would exhaust the pool."""
    session = SimpleNamespace(lab_instance_id=uuid4(), use_reranker=False, system_prompt="test")
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    history = AsyncMock()
    monkeypatch.setattr(ChatRepository, "append_messages", history)
    db = request_db()
    seen = []

    async def stream(**kwargs):
        seen.append(db.rollback.await_count)
        yield {"text": "answer"}

    monkeypatch.setattr(rag_service, "run_agent_turn", stream)
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(session.lab_instance_id))
    async for _ in rag_service.generate_chat_response_sse(
        db, uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
    ):
        pass
    assert seen == [1]
    history.assert_awaited_once()


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
            request_db(), uuid4(), uuid4(), "Question", LLMQueueManager(), store, user, model
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

    monkeypatch.setattr(rag_service, "run_agent_turn", stream)
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(session.lab_instance_id))
    parts = [
        part
        async for part in rag_service.generate_chat_response_sse(
            request_db(), uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
        )
    ]
    assert parts[-1] == "data: [DONE]\n\n"
    assert json.loads(parts[0][6:]) == {"error": rag_service.CHAT_ERROR_MESSAGE}
    assert not any(secret in "".join(parts) for secret in ("hunter2", "10.0.0.5", "postgresql"))
    assert any(internal in record.getMessage() + str(record.exc_info) for record in caplog.records)
    history.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_ex03_secret_reaches_the_model_even_after_prompt_patch(monkeypatch, no_exercise_secret):
    session = SimpleNamespace(
        lab_instance_id=uuid4(), use_reranker=False, system_prompt="You are a highly secure AI. (Prompt Patch)"
    )
    monkeypatch.setattr(rag_service, "get_authorized_session", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "get_or_create_thread", AsyncMock())
    monkeypatch.setattr(ChatRepository, "append_messages", AsyncMock())
    no_exercise_secret.return_value = "flag{" + "a" * 32 + "}"
    captured = {}

    async def stream(**kwargs):
        captured.update(kwargs)
        yield {"text": "answer"}

    monkeypatch.setattr(rag_service, "run_agent_turn", stream)
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [[]]}))
    user = dict(id=str(uuid4()), sub="student", role="red_team", lab_instance_id=str(session.lab_instance_id))
    db = request_db()
    async for _ in rag_service.generate_chat_response_sse(
        db, uuid4(), uuid4(), "question", LLMQueueManager(), store, user, object()
    ):
        pass
    no_exercise_secret.assert_awaited_once_with(db, session.lab_instance_id, "EX-03")
    assert "(Prompt Patch)" in captured["system_prompt"]
    assert "flag{" + "a" * 32 + "}" in captured["system_prompt"]
    assert "flag{" not in captured["human_prompt"]


def test_confidential_note_follows_any_system_instruction():
    note = "flag{" + "b" * 32 + "}"
    for instruction in (None, "Custom instruction"):
        system, human = PromptBuilder.build_prompts("q", system_instruction=instruction, confidential_note=note)
        assert note in system and note not in human
        if instruction:
            assert system.index(instruction) < system.index(note)
    assert "confidential" not in PromptBuilder.build_prompts("q")[0].lower()


def test_prompt_tells_the_model_to_search_documents_with_the_tool():
    for instruction in (None, "Custom instruction"):
        system, human = PromptBuilder.build_prompts(
            "When are exams?", username="student", system_instruction=instruction
        )
        assert "search_documents" in system  # also after Prompt Patch replaces the instruction
        assert "CONTEXT" not in system + human
        assert "When are exams?" in human and "student" in human
