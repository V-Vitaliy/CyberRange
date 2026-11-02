"""Chat streaming and tool execution; real adapters with offline HTTP doubles."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessageChunk

from app.core import llm_engine
from app.core.tools import send_email
from tests.openai_doubles import assert_request, make_model, sse


async def run_stream(chunks):
    async def stream(messages):
        for chunk in chunks:
            yield chunk

    model = Mock()
    model.bind_tools.return_value = SimpleNamespace(astream=stream)
    output = "".join(
        [part async for part in llm_engine.generate_unified_stream(model, "system", "question", object(), uuid4())]
    )
    model.bind_tools.assert_called_once_with([send_email])
    return output


def tool_chunk(args, name="send_email", index=0, call_id="call1"):
    return AIMessageChunk(content="", tool_call_chunks=[{"name": name, "args": args, "id": call_id, "index": index}])


@pytest.mark.asyncio
async def test_plain_answer_keeps_streaming_without_email(harness):
    audit, delivery = harness
    assert await run_stream([AIMessageChunk(content="Hello "), AIMessageChunk(content="student")]) == "Hello student"
    delivery.assert_not_called()
    audit.assert_not_awaited()


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
    output = "".join(
        [part async for part in llm_engine.generate_unified_stream(model, "system", "question", object(), uuid4())]
    )
    assert output == "Local answer"
    harness[1].assert_not_called()


def test_real_groq_adapter_binds_email_schema_without_network(monkeypatch):
    monkeypatch.setattr(llm_engine.settings, "GROQ_API_KEY", "unused-test-key")
    model = llm_engine.init_groq()
    bound = model.bind_tools([send_email])
    schema = bound.kwargs["tools"][0]["function"]
    assert schema["name"] == "send_email"
    assert set(schema["parameters"]["required"]) == {"to_address", "subject", "body"}
    assert not model.model_kwargs.get("tools")


@pytest.mark.parametrize("url,model", [("", "test-model"), ("http://inference.invalid/v1", ""), (" ", " ")])
def test_http_provider_requires_explicit_endpoint_and_model(monkeypatch, url, model):
    monkeypatch.setattr(llm_engine.settings, "LLM_BASE_URL", url)
    monkeypatch.setattr(llm_engine.settings, "LLM_MODEL", model)
    with pytest.raises(ValueError, match="LLM_BASE_URL and LLM_MODEL must be nonempty"):
        llm_engine.init_openai_compatible()


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
    monkeypatch.setattr(llm_engine.settings, "SMTP_HOST", "smtp.lab.invalid")
    monkeypatch.setattr(llm_engine.settings, "EMAIL_ALLOWED_DOMAINS", "lab.local")
    output = await run_stream([tool_chunk(json.dumps(dict(to_address=address, subject="Test", body=body)))])
    assert send.called is (delivery == "sent")
    assert audit.call_args.kwargs["event_type"] == event_type
    assert audit.call_args.kwargs["payload"]["delivery"] == delivery
    assert ("blocked" in output) is (delivery == "blocked")
