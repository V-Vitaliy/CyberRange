"""Agent tool registry: schemas, strict validation, handlers and SIEM audit."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict

from app.agents.tools import registry
from app.agents.tools.base import AgentTool, ToolResult
from app.agents.tools.email_tool import send_email
from app.chat import llm_engine


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


async def chat(chunks):
    async def stream(messages):
        for chunk in chunks:
            yield chunk

    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    output = "".join(
        [part async for part in llm_engine.generate_unified_stream(model, "system", "question", object(), uuid4())]
    )
    return model, output


def call(name, args):
    return AIMessageChunk(content="", tool_call_chunks=[{"name": name, "args": args, "id": "call1", "index": 0}])


def test_registry_exposes_send_email_as_high_risk_tool():
    assert registry.TOOLS["send_email"].schema is send_email
    assert registry.TOOLS["send_email"].risk == "high"
    assert registry.tool_schemas() == [send_email]


@pytest.mark.asyncio
async def test_registered_tool_runs_through_unchanged_chat_loop(harness, ticket_tool):
    audit, delivery = harness
    model, output = await chat([call("lookup_ticket", '{"ticket_id": 7}')])
    model.bind_tools.assert_called_once_with([send_email, lookup_ticket])
    ticket_tool.assert_awaited_once_with(TicketArguments(ticket_id=7))
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
