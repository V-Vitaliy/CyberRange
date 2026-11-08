"""Model selection and initialization; adapters are built without network or weights."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agents.tools.email_tool import send_email
from app.llm import provider


@pytest.mark.parametrize(
    "selected,groq_key,expected",
    [
        ("openai_compatible", "unused", "init_openai_compatible"),
        ("openai_compatible", "", "init_openai_compatible"),
        ("", "test-key", "init_groq"),
        ("", "", "init_gguf"),
    ],
)
def test_factory_selects_one_provider(monkeypatch, selected, groq_key, expected):
    monkeypatch.setattr(provider.settings, "LLM_PROVIDER", selected)
    monkeypatch.setattr(provider.settings, "GROQ_API_KEY", groq_key)
    model = object()
    initializers = {}
    for name in ("init_openai_compatible", "init_groq", "init_gguf"):
        initializers[name] = Mock(return_value=model if name == expected else None)
        monkeypatch.setattr(provider, name, initializers[name])
    assert provider.create_llm() is model
    for name, initializer in initializers.items():
        if name == expected:
            initializer.assert_called_once_with()
        else:
            initializer.assert_not_called()


def test_gguf_provider_loads_configured_local_model(monkeypatch):
    adapter = Mock()
    monkeypatch.setattr(provider, "ChatLlamaCpp", adapter)
    monkeypatch.setattr(provider.settings, "LLM_N_THREADS", 2)
    monkeypatch.setattr(provider.settings, "LLM_N_BATCH", 64)
    monkeypatch.setattr(provider.settings, "LLM_N_GPU_LAYERS", 0)
    monkeypatch.setattr(provider.settings, "LLM_N_CTX", 1024)
    assert provider.init_gguf() is adapter.return_value
    kwargs = adapter.call_args.kwargs
    assert kwargs["model_path"] == provider.MODEL_PATH
    # The relative LLM_MODEL_PATH keeps resolving to the same file as before the move.
    core_dir = Path(provider.__file__).parents[1] / "core"
    assert Path(provider.MODEL_PATH).resolve() == (core_dir / provider.settings.LLM_MODEL_PATH).resolve()
    assert (kwargs["n_threads"], kwargs["n_batch"], kwargs["n_gpu_layers"], kwargs["n_ctx"]) == (2, 64, 0, 1024)
    assert kwargs["streaming"] is True


def test_real_groq_adapter_binds_email_schema_without_network(monkeypatch):
    monkeypatch.setattr(provider.settings, "GROQ_API_KEY", "unused-test-key")
    model = provider.init_groq()
    bound = model.bind_tools([send_email])
    schema = bound.kwargs["tools"][0]["function"]
    assert schema["name"] == "send_email"
    assert set(schema["parameters"]["required"]) == {"to_address", "subject", "body"}
    assert not model.model_kwargs.get("tools")


@pytest.mark.parametrize("url,model", [("", "test-model"), ("http://inference.invalid/v1", ""), (" ", " ")])
def test_http_provider_requires_explicit_endpoint_and_model(monkeypatch, url, model):
    monkeypatch.setattr(provider.settings, "LLM_BASE_URL", url)
    monkeypatch.setattr(provider.settings, "LLM_MODEL", model)
    with pytest.raises(ValueError, match="LLM_BASE_URL and LLM_MODEL must be nonempty"):
        provider.init_openai_compatible()


def test_request_dependency_returns_application_model():
    model = object()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llm=model)))
    assert provider.get_llm_instance(request) is model


@pytest.mark.parametrize("limit", [2, 4])
def test_in_process_gguf_refuses_parallel_generations(monkeypatch, limit):
    monkeypatch.setattr(provider.settings, "LLM_PROVIDER", "")
    monkeypatch.setattr(provider.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(provider.settings, "LLM_MAX_CONCURRENCY", limit)
    gguf = Mock()
    monkeypatch.setattr(provider, "init_gguf", gguf)
    with pytest.raises(ValueError, match="LLM_MAX_CONCURRENCY"):
        provider.create_llm()
    gguf.assert_not_called()  # fails before the weights are loaded


@pytest.mark.parametrize(
    "selected,groq_key,initializer", [("openai_compatible", "", "init_openai_compatible"), ("", "key", "init_groq")]
)
def test_external_providers_accept_the_configured_concurrency(monkeypatch, selected, groq_key, initializer):
    monkeypatch.setattr(provider.settings, "LLM_PROVIDER", selected)
    monkeypatch.setattr(provider.settings, "GROQ_API_KEY", groq_key)
    monkeypatch.setattr(provider.settings, "LLM_MAX_CONCURRENCY", 4)
    model = object()
    monkeypatch.setattr(provider, initializer, Mock(return_value=model))
    assert provider.create_llm() is model
