"""Offline HTTP doubles for the real OpenAI-compatible adapter."""

import json

import httpx

from app.llm import provider


def make_model(monkeypatch, handler):
    monkeypatch.setattr(provider.settings, "LLM_BASE_URL", "http://local.invalid/v1")
    monkeypatch.setattr(provider.settings, "LLM_MODEL", "test-model")
    monkeypatch.setattr(provider.settings, "LLM_API_KEY", "")
    monkeypatch.setattr(provider.settings, "LLM_MAX_TOKENS", 3072)
    original = provider.ChatOpenAI

    def adapter(**kwargs):
        return original(**kwargs, http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(provider, "ChatOpenAI", adapter)
    return provider.init_openai_compatible()


def assert_request(request, stream=True):
    assert request.method == "POST"
    assert str(request.url) == "http://local.invalid/v1/chat/completions"
    payload = json.loads(request.content)
    assert payload["model"] == "test-model"
    assert payload["max_completion_tokens"] == 3072
    assert not any(value is None for value in payload.values())
    assert not {"reasoning_format", "service_tier", "reasoning_effort"} & payload.keys()
    assert payload["stream"] is stream
    assert "stream_options" not in payload
    return payload


def sse(delta, finish_reason=None):
    body = {
        "id": "offline",
        "model": "test-model",
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return "data: " + json.dumps(body) + "\n\n"
