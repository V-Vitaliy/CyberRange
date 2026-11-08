"""Chat streaming and tool execution; real adapters with offline HTTP doubles."""

import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.agents.tools import email_tool
from app.agents.tools.email_tool import send_email
from app.chat import llm_engine
from app.core.queue_manager import LLMQueueManager
from tests.openai_doubles import assert_request, make_model, sse

LAB = str(uuid4())
EMAIL = json.dumps(dict(to_address="student@example.invalid", subject="Test", body="Hello"))


class ScriptedModel:
    """Bound chat model double: each call plays the next scripted turn and records the messages it got."""

    def __init__(self, *turns):
        self.turns, self.calls = list(turns), []

    async def astream(self, messages):
        self.calls.append(list(messages))
        for chunk in self.turns.pop(0) if self.turns else [AIMessageChunk(content="Done.")]:
            yield chunk


async def run_turn(*turns, queue=None):
    scripted = ScriptedModel(*turns)
    model = Mock()
    model.bind_tools.return_value = scripted
    events = [
        event async for event in llm_engine.run_agent_turn(model, "system", "question", LAB, queue or LLMQueueManager())
    ]
    model.bind_tools.assert_called_once_with([send_email])
    return events, scripted


def text(events):
    return "".join(event["text"] for event in events if "text" in event)


def steps(events):
    return [event["step"] for event in events if "step" in event]


async def run_stream(chunks):
    """Answer text plus every tool outcome of a turn whose first model step plays `chunks`."""
    events, _ = await run_turn(chunks)
    return text(events) + "".join(step["outcome"] for step in steps(events))


@pytest.fixture(autouse=True)
def turn_log(monkeypatch):
    log = AsyncMock()
    monkeypatch.setattr(llm_engine, "log_security_event", log, raising=False)
    return log


def tool_chunk(args, name="send_email", index=0, call_id="call1"):
    return AIMessageChunk(content="", tool_call_chunks=[{"name": name, "args": args, "id": call_id, "index": index}])


@pytest.mark.asyncio
async def test_plain_answer_keeps_streaming_without_email(harness, turn_log):
    audit, delivery = harness
    assert await run_stream([AIMessageChunk(content="Hello "), AIMessageChunk(content="student")]) == "Hello student"
    delivery.assert_not_called()
    audit.assert_not_awaited()
    turn_log.assert_not_awaited()  # a turn without tools adds no AGENT_TURN event


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks",
    [
        [],
        [AIMessageChunk(content="", additional_kwargs={"reasoning_content": "reasoning only"})],
        [AIMessageChunk(content="", response_metadata={"finish_reason": "length"})],
        [AIMessageChunk(content="partial answer", response_metadata={"finish_reason": "length"})],
        [
            AIMessageChunk(
                content="",
                response_metadata={"finish_reason": "length"},
                tool_call_chunks=[
                    {
                        "name": "send_email",
                        "id": "call1",
                        "index": 0,
                        "args": '{"to_address":"student@example.invalid","subject":"Test","body":"Hello"}',
                    }
                ],
            )
        ],
    ],
)
async def test_empty_or_truncated_model_output_fails_without_tool_execution(harness, chunks):
    with pytest.raises(RuntimeError, match="Model (returned no answer|output limit reached)"):
        await run_stream(chunks)
    harness[0].assert_not_awaited()
    harness[1].assert_not_called()


@pytest.mark.asyncio
async def test_split_tool_arguments_execute_once_and_report_simulation(harness):
    audit, delivery = harness
    output = await run_stream(
        [
            tool_chunk('{"to_address":"student@example.invalid",'),
            tool_chunk('"subject":"Test","body":"Hello"}', name=None, call_id=None),
        ]
    )
    delivery.assert_called_once_with("student@example.invalid", "Test", "Hello")
    assert "No email was sent" in output
    assert audit.call_args.kwargs["event_type"] == "AI_EMAIL_SIMULATED"
    assert audit.call_args.kwargs["payload"]["delivery"] == "simulated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args,name",
    [
        ('{"to_address":"x"', "send_email"),
        ("{}", "send_email"),
        ("[]", "send_email"),
        ('{"to_address":42,"subject":"x","body":"x"}', "send_email"),
        ('{"to_address":"x","subject":"x","body":"x","extra":true}', "send_email"),
        ('{"to_address":"x","subject":"x","body":"x"}', "unknown"),
    ],
)
async def test_invalid_tool_never_sends(harness, args, name):
    audit, delivery = harness
    assert "invalid tool or arguments" in await run_stream([tool_chunk(args, name)])
    delivery.assert_not_called()
    assert audit.call_args.kwargs["event_type"] == "AI_TOOL_FAILED"


@pytest.mark.asyncio
async def test_multiple_tool_calls_are_not_mixed(harness):
    _, delivery = harness
    chunks = [
        tool_chunk(
            json.dumps(dict(to_address=f"{n}@example.invalid", subject="Test", body=str(n))),
            index=n,
            call_id=f"call{n}",
        )
        for n in range(2)
    ]
    await run_stream(chunks)
    assert delivery.call_count == 2
    assert delivery.call_args_list[0].args[0] == "0@example.invalid"
    assert delivery.call_args_list[1].args[0] == "1@example.invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [False, RuntimeError("SMTP unavailable")])
async def test_failed_delivery_is_not_logged_as_sent(harness, outcome):
    audit, delivery = harness
    if isinstance(outcome, Exception):
        delivery.side_effect = outcome
    else:
        delivery.return_value = outcome
    output = await run_stream(
        [tool_chunk(json.dumps(dict(to_address="test@example.invalid", subject="Test", body="flag{test}")))]
    )
    assert "failed" in output
    assert audit.call_args.kwargs["event_type"] == "AI_EMAIL_FAILED"
    assert audit.call_args.kwargs["payload"]["exfiltration_attempt"] is True


@pytest.mark.asyncio
async def test_simulated_exfiltration_remains_visible_to_siem(harness):
    audit, _ = harness
    await run_stream(
        [tool_chunk(json.dumps(dict(to_address="test@example.invalid", subject="Test", body="flag{test}")))]
    )
    assert audit.call_args.kwargs["event_type"] == "AI_DATA_EXFILTRATION"
    assert audit.call_args.kwargs["payload"]["delivery"] == "simulated"


@pytest.mark.asyncio
async def test_model_without_tools_retains_text_chat(harness):
    async def stream(messages):
        yield AIMessageChunk(content="Local answer")

    model = Mock()
    model.bind_tools.side_effect = NotImplementedError()
    model.astream = stream
    events = [event async for event in llm_engine.run_agent_turn(model, "system", "question", LAB, LLMQueueManager())]
    assert text(events) == "Local answer"
    harness[1].assert_not_called()


@pytest.mark.asyncio
async def test_real_adapter_streams_unicode_and_keeps_chat_tools(monkeypatch):
    def handler(request):
        payload = assert_request(request)
        assert payload["tools"][0]["function"]["name"] == "send_email"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=sse({"role": "assistant", "content": "Cześć "})
            + sse({"content": "Łódź"}, "stop")
            + "data: [DONE]\n\n",
        )

    model = make_model(monkeypatch, handler)
    assert "".join([chunk.content async for chunk in model.bind_tools([send_email]).astream("hello")]) == "Cześć Łódź"


@pytest.mark.asyncio
async def test_server_failure_is_propagated(monkeypatch):
    def handler(request):
        assert_request(request)
        return httpx.Response(
            400, json={"error": {"message": "Unsupported tool call", "type": "invalid_request_error"}}
        )

    model = make_model(monkeypatch, handler)
    from openai import BadRequestError

    with pytest.raises(BadRequestError):
        async for _ in model.bind_tools([send_email]).astream("hello"):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address,body,event_type,delivery",
    [
        ("attacker@example.com", "hello", "AI_EMAIL_BLOCKED", "blocked"),
        ("attacker@example.com", "flag{test}", "AI_DATA_EXFILTRATION", "blocked"),
        ("student@lab.local", "hello", "AI_EMAIL_SENT", "sent"),
    ],
)
async def test_real_smtp_only_delivers_to_lab_domains(harness, monkeypatch, address, body, event_type, delivery):
    audit, send = harness
    monkeypatch.setattr(email_tool.settings, "SMTP_HOST", "smtp.lab.invalid")
    monkeypatch.setattr(email_tool.settings, "EMAIL_ALLOWED_DOMAINS", "lab.local")
    output = await run_stream([tool_chunk(json.dumps(dict(to_address=address, subject="Test", body=body)))])
    assert send.called is (delivery == "sent")
    assert audit.call_args.kwargs["event_type"] == event_type
    assert audit.call_args.kwargs["payload"]["delivery"] == delivery
    assert ("blocked" in output) is (delivery == "blocked")


@pytest.mark.asyncio
async def test_tool_result_goes_back_to_the_model_which_then_answers(harness, turn_log):
    events, model = await run_turn([tool_chunk(EMAIL)], [AIMessageChunk(content="I tried to send it.")])
    assert len(model.calls) == 2
    *_, request, result = model.calls[1]
    assert isinstance(request, AIMessage) and request.tool_calls[0]["id"] == "call1"
    assert isinstance(result, ToolMessage) and result.tool_call_id == "call1"
    assert "No email was sent" in result.content
    assert text(events) == "I tried to send it."
    [step] = steps(events)
    assert (step["step"], step["tool"]) == (1, "send_email")
    assert "No email was sent" in step["outcome"]


@pytest.mark.asyncio
async def test_agent_turn_is_logged_once_with_every_step(harness, turn_log):
    audit, delivery = harness
    await run_turn([tool_chunk(EMAIL)], [tool_chunk(EMAIL, call_id="call2")], [AIMessageChunk(content="Done.")])
    assert delivery.call_count == 2
    assert audit.await_count == 2  # one event per executed action, as before
    turn_log.assert_awaited_once()
    event = turn_log.call_args.kwargs
    assert (event["lab_instance_id"], event["event_type"], event["source_ip"]) == (LAB, "AGENT_TURN", "LLM_AGENT")
    assert event["payload"]["status"] == "answered"
    assert [(s["step"], s["tool"]) for s in event["payload"]["steps"]] == [(1, "send_email"), (2, "send_email")]


@pytest.mark.asyncio
async def test_step_limit_ends_the_turn_with_a_generic_answer(harness, turn_log):
    _, delivery = harness
    limit = llm_engine.AGENT_MAX_STEPS
    events, model = await run_turn(*[[tool_chunk(EMAIL, call_id=f"call{n}")] for n in range(limit + 3)])
    assert len(model.calls) == limit
    assert delivery.call_count == limit
    assert text(events) == llm_engine.AGENT_STEP_LIMIT_MESSAGE
    payload = turn_log.call_args.kwargs["payload"]
    assert (payload["status"], len(payload["steps"])) == ("step_limit", limit)


@pytest.mark.asyncio
async def test_logged_and_streamed_steps_are_truncated(harness, turn_log):
    long_email = json.dumps(dict(to_address="student@example.invalid", subject="Test", body="x" * 5000))
    events, _ = await run_turn([tool_chunk(long_email)])
    for step in (steps(events)[0], turn_log.call_args.kwargs["payload"]["steps"][0]):
        assert len(step["arguments"]) <= llm_engine.AGENT_LOG_VALUE_CHARS + 1
        assert len(step["outcome"]) <= llm_engine.AGENT_LOG_VALUE_CHARS + 1


@pytest.mark.asyncio
async def test_invalid_tool_call_is_reported_to_the_model(harness, turn_log):
    audit, delivery = harness
    events, model = await run_turn([tool_chunk("{}")], [AIMessageChunk(content="Sorry.")])
    delivery.assert_not_called()
    assert audit.call_args.kwargs["event_type"] == "AI_TOOL_FAILED"
    assert "invalid tool or arguments" in model.calls[1][-1].content
    assert text(events) == "Sorry."


@pytest.mark.asyncio
async def test_failed_step_after_a_tool_call_is_still_logged(harness, turn_log):
    with pytest.raises(RuntimeError, match="no answer"):
        await run_turn([tool_chunk(EMAIL)], [])
    payload = turn_log.call_args.kwargs["payload"]
    assert (payload["status"], len(payload["steps"])) == ("error", 1)


@pytest.mark.asyncio
async def test_gpu_lock_is_held_only_while_the_model_runs(harness, turn_log):
    _, delivery = harness
    queue = LLMQueueManager()
    seen = []

    class LockAwareModel(ScriptedModel):
        async def astream(self, messages):
            seen.append(("model", queue.gpu_lock.locked()))
            async for chunk in super().astream(messages):
                yield chunk

    delivery.side_effect = lambda *args: seen.append(("tool", queue.gpu_lock.locked())) or True
    model = Mock()
    model.bind_tools.return_value = LockAwareModel([tool_chunk(EMAIL)], [AIMessageChunk(content="Done.")])
    [_ async for _ in llm_engine.run_agent_turn(model, "system", "question", LAB, queue)]
    # Between steps the model is free for the Auto-SOC and other chats.
    assert seen == [("model", True), ("tool", False), ("model", True)]
    assert not queue.gpu_lock.locked()
