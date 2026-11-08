"""Agent tool registry: schemas, strict validation, handlers and SIEM audit."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict

from app.agents.tools import document_search_tool, registry
from app.agents.tools.base import AgentTool, ToolContext, ToolResult
from app.agents.tools.document_search_tool import search_documents
from app.agents.tools.email_tool import send_email
from app.chat import llm_engine
from app.core.queue_manager import LLMQueueManager
from tests.unit.test_llm_engine import ScriptedModel


class TicketArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: int


@tool(args_schema=TicketArguments)
def lookup_ticket(ticket_id: int) -> str:
    """Read one helpdesk ticket."""
    raise RuntimeError("Executed through the registry")


@pytest.fixture
def ticket_tool(monkeypatch):
    handler = AsyncMock(return_value=ToolResult("AI_TICKET_READ", {"ticket_id": 7}, "\n\nTicket 7 is open."))
    monkeypatch.setitem(registry.TOOLS, "lookup_ticket", AgentTool(schema=lookup_ticket, risk="low", handler=handler))
    return handler


@pytest.fixture
def audit(monkeypatch):
    """Replaces only the tool audit; unlike `harness`, keeps the real asyncio.to_thread."""
    monkeypatch.setattr(registry, "log_security_event", AsyncMock())


@pytest.fixture(autouse=True)
def turn_log(monkeypatch):
    monkeypatch.setattr(llm_engine, "log_security_event", AsyncMock(), raising=False)


def context(role="red_team", use_reranker=False, documents=()):
    """A turn context whose vector store returns `documents` for any query."""
    store = SimpleNamespace(search_documents=Mock(return_value={"documents": [list(documents)]}))
    return ToolContext(lab_instance_id=str(uuid4()), role=role, use_reranker=use_reranker, vector_store=store)


async def chat(chunks, turn=None):
    """Run one agent turn whose first step plays `chunks`; return the model and the tool outcomes."""
    scripted = ScriptedModel(chunks)
    model = Mock()
    model.bind_tools.return_value = scripted
    events = [
        event
        async for event in llm_engine.run_agent_turn(model, "system", "question", turn or context(), LLMQueueManager())
    ]
    return model, "".join(event["step"]["outcome"] for event in events if "step" in event)


async def search(turn, args='{"query": "exam dates"}'):
    """Execute one search_documents call directly; the outcome is not truncated by the agent step log."""
    return await registry.execute_tool_call({"name": "search_documents", "args": args, "id": "call1", "index": 0}, turn)


def call(name, args):
    return AIMessageChunk(content="", tool_call_chunks=[{"name": name, "args": args, "id": "call1", "index": 0}])


def test_registry_exposes_email_and_document_search_tools():
    assert registry.TOOLS["send_email"].schema is send_email
    assert registry.TOOLS["send_email"].risk == "high"
    assert registry.TOOLS["search_documents"].schema is search_documents
    assert registry.TOOLS["search_documents"].risk == "low"
    assert registry.tool_schemas() == [send_email, search_documents]


@pytest.mark.asyncio
async def test_registered_tool_runs_through_unchanged_chat_loop(harness, ticket_tool):
    audit, delivery = harness
    turn = context()
    model, output = await chat([call("lookup_ticket", '{"ticket_id": 7}')], turn)
    model.bind_tools.assert_called_once_with([send_email, search_documents, lookup_ticket])
    ticket_tool.assert_awaited_once_with(TicketArguments(ticket_id=7), turn)  # handlers get the turn context
    assert output == "\n\nTicket 7 is open."
    audit.assert_awaited_once()
    assert audit.call_args.kwargs["event_type"] == "AI_TICKET_READ"
    assert audit.call_args.kwargs["payload"] == {"ticket_id": 7, "tool": "lookup_ticket", "risk": "low"}
    assert audit.call_args.kwargs["source_ip"] == "LLM_AGENT"
    delivery.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("args", ['{"ticket_id": "7"}', '{"ticket_id": 7, "extra": 1}', '{"ticket_id": 7', "[]"])
async def test_registered_tool_arguments_are_strictly_validated(harness, ticket_tool, args):
    audit, _ = harness
    _, output = await chat([call("lookup_ticket", args)])
    ticket_tool.assert_not_awaited()
    assert "invalid tool or arguments" in output
    assert audit.call_args.kwargs["event_type"] == "AI_TOOL_FAILED"
    assert audit.call_args.kwargs["payload"]["tool"] == "lookup_ticket"


@pytest.mark.asyncio
async def test_email_audit_names_tool_and_risk(harness):
    audit, delivery = harness
    args = json.dumps(dict(to_address="student@example.invalid", subject="Test", body="Hello"))
    await chat([call("send_email", args)])
    delivery.assert_called_once_with("student@example.invalid", "Test", "Hello")
    assert audit.call_args.kwargs["payload"] == {
        "to": "student@example.invalid",
        "subject": "Test",
        "body": "Hello",
        "delivery": "simulated",
        "exfiltration_attempt": False,
        "tool": "send_email",
        "risk": "high",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["red_team", "admin"])
async def test_document_search_limits_both_roles_to_their_lab(harness, role):
    turn = context(role=role, documents=["Exams start on Monday."])
    output = await search(turn)
    query = turn.vector_store.search_documents.call_args
    lab_filter = {"lab_instance_id": turn.lab_instance_id}
    assert query.kwargs["where"] == (
        lab_filter if role == "admin" else {"$and": [lab_filter, {"access_level": "public"}]}
    )
    assert query.args[0] == "exam dates"
    assert query.kwargs["n_results"] == document_search_tool.DOCUMENT_SEARCH_MAX_CHUNKS
    assert "Exams start on Monday." in output


@pytest.mark.asyncio
async def test_document_search_audit_records_query_and_chunk_count(harness):
    audit, _ = harness
    turn = context(documents=["first", "second"])
    await search(turn)
    audit.assert_awaited_once()
    event = audit.call_args.kwargs
    assert (event["lab_instance_id"], event["event_type"], event["source_ip"]) == (
        turn.lab_instance_id,
        "AI_DOCUMENT_SEARCH",
        "LLM_AGENT",
    )
    assert event["payload"] == {
        "query": "exam dates",
        "chunks": 2,
        "reranked": False,
        "tool": "search_documents",
        "risk": "low",
    }


@pytest.mark.asyncio
async def test_document_search_without_results_says_so(harness):
    audit, _ = harness
    output = await search(context(documents=[]))
    assert "No matching documents" in output
    assert audit.call_args.kwargs["payload"]["chunks"] == 0


@pytest.mark.asyncio
async def test_document_search_result_is_bounded(harness):
    audit, _ = harness
    limit = document_search_tool.DOCUMENT_SEARCH_MAX_CHUNKS
    documents = [f"chunk-{n}:" + "x" * 3000 for n in range(limit + 3)]
    output = await search(context(documents=documents))
    assert len(output) <= document_search_tool.DOCUMENT_SEARCH_MAX_CHARS
    assert "chunk-0:" in output
    assert f"chunk-{limit}:" not in output
    assert audit.call_args.kwargs["payload"]["chunks"] == limit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    ["{}", '{"query": 1}', '{"query": ""}', '{"query": "x", "extra": 1}', json.dumps({"query": "x" * 501}), "[]"],
)
async def test_document_search_arguments_are_strictly_validated(harness, args):
    audit, _ = harness
    turn = context(documents=["secret"])
    assert "invalid tool or arguments" in await search(turn, args)
    turn.vector_store.search_documents.assert_not_called()
    assert audit.call_args.kwargs["event_type"] == "AI_TOOL_FAILED"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_purchased_reranker_filters_tool_results(harness, monkeypatch, enabled):
    audit, _ = harness
    reranker = Mock()
    reranker.predict.return_value = [-1, 2]
    monkeypatch.setattr(document_search_tool, "get_reranker", Mock(return_value=reranker))
    output = await search(context(use_reranker=enabled, documents=["discarded context", "retained context"]))
    assert "retained context" in output
    assert ("discarded context" in output) is not enabled
    assert reranker.predict.called is enabled
    if enabled:
        assert reranker.predict.call_args.args[0][0] == ["exam dates", "discarded context"]
    assert audit.call_args.kwargs["payload"]["reranked"] is enabled


@pytest.mark.asyncio
async def test_document_search_runs_off_the_event_loop(audit):
    threads = []

    def lookup(*args, **kwargs):
        threads.append(threading.get_ident())
        assert not threading.current_thread().name.startswith("etl")
        return {"documents": [["context"]]}

    turn = context(role="admin")
    turn.vector_store.search_documents.side_effect = lookup
    await search(turn)
    assert threads and threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_reranker_loading_and_scoring_run_off_the_event_loop(audit, monkeypatch):
    threads = {}

    def predict(pairs):
        threads["predict"] = threading.get_ident()
        return [1.0 for _ in pairs]

    def load():
        threads["load"] = threading.get_ident()
        return SimpleNamespace(predict=predict)

    monkeypatch.setattr(document_search_tool, "get_reranker", load)
    await search(context(role="admin", use_reranker=True, documents=["context"]))
    assert threading.get_ident() not in (threads.get("load"), threads.get("predict"))
    assert set(threads) == {"load", "predict"}


@pytest.mark.asyncio
async def test_email_handler_keeps_its_behaviour_with_the_turn_context(harness):
    audit, delivery = harness
    args = json.dumps(dict(to_address="student@example.invalid", subject="Test", body="Hello"))
    output = await registry.execute_tool_call({"name": "send_email", "args": args, "id": "c", "index": 0}, context())
    assert "No email was sent" in output
    delivery.assert_called_once_with("student@example.invalid", "Test", "Hello")
    assert audit.call_args.kwargs["event_type"] == "AI_EMAIL_SIMULATED"


@pytest.mark.asyncio
async def test_failing_tool_handler_is_audited_and_reported_generically(harness, ticket_tool, caplog):
    audit, _ = harness
    ticket_tool.side_effect = RuntimeError("connect to 10.0.0.5 failed")
    turn = context()
    output = await registry.execute_tool_call(
        {"name": "lookup_ticket", "args": '{"ticket_id": 7}', "id": "c", "index": 0}, turn
    )
    assert "Tool execution failed" in output
    assert "10.0.0.5" not in output
    audit.assert_awaited_once()
    event = audit.call_args.kwargs
    assert (event["lab_instance_id"], event["event_type"], event["source_ip"]) == (
        turn.lab_instance_id,
        "AI_TOOL_FAILED",
        "LLM_AGENT",
    )
    assert event["payload"] == {"tool": "lookup_ticket", "reason": "Tool execution failed"}
    assert any("10.0.0.5" in str(record.exc_info) for record in caplog.records)  # details stay in the server log


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["search", "reranker"])
async def test_document_search_failures_are_audited(harness, monkeypatch, failure):
    audit, _ = harness
    turn = context(use_reranker=True, documents=["context"])
    if failure == "search":
        turn.vector_store.search_documents.side_effect = RuntimeError("Chroma unavailable")
    else:
        monkeypatch.setattr(document_search_tool, "get_reranker", Mock(side_effect=OSError("model missing")))
    output = await search(turn)
    assert "Tool execution failed" in output
    assert audit.call_args.kwargs["event_type"] == "AI_TOOL_FAILED"
    assert audit.call_args.kwargs["payload"]["tool"] == "search_documents"
