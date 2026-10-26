import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.runnables import RunnableLambda

from app import main
from app.agents.soc_agent import AnalysisOutput


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", [False, True])
async def test_startup_uses_selected_model_and_cleans_up_soc(monkeypatch, cloud):
    model = Mock()
    model.with_structured_output.return_value = RunnableLambda(lambda _: AnalysisOutput(is_malicious=False, reasoning="test"))
    local, groq = Mock(return_value=model), Mock(return_value=model)
    monkeypatch.setattr(main.settings, "GROQ_API_KEY", "test-key" if cloud else "")
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", "")
    monkeypatch.setattr(main, "init_llm", local)
    monkeypatch.setattr(main, "init_groq", groq)
    monkeypatch.setattr(main, "VectorStore", Mock())
    cancelled = asyncio.Event()

    async def worker(graph, queue):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "run_soc_analyst_loop", worker)
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert main.red_app.state.llm is model
        assert main.blue_app.state.llm is model
    assert cancelled.is_set()
    (groq if cloud else local).assert_called_once()
    (local if cloud else groq).assert_not_called()


@pytest.mark.asyncio
async def test_unsupported_local_soc_does_not_prevent_local_api_startup(monkeypatch):
    model = Mock()
    model.with_structured_output.side_effect = NotImplementedError("no structured output")
    monkeypatch.setattr(main.settings, "GROQ_API_KEY", "")
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", "")
    monkeypatch.setattr(main, "init_llm", Mock(return_value=model))
    cloud = Mock(side_effect=AssertionError("must not initialize Groq"))
    monkeypatch.setattr(main, "init_groq", cloud)
    monkeypatch.setattr(main, "VectorStore", Mock())
    worker = AsyncMock()
    monkeypatch.setattr(main, "run_soc_analyst_loop", worker)
    async with main.lifespan(main.app):
        assert main.app.state.llm is model
    worker.assert_not_called()
    cloud.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_openai_provider_takes_precedence_over_groq(monkeypatch):
    model = Mock()
    model.with_structured_output.side_effect = NotImplementedError()
    monkeypatch.setattr(main.settings, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(main.settings, "GROQ_API_KEY", "unused")
    local, groq, compatible = Mock(), Mock(), Mock(return_value=model)
    monkeypatch.setattr(main, "init_llm", local)
    monkeypatch.setattr(main, "init_groq", groq)
    monkeypatch.setattr(main, "init_openai_compatible", compatible)
    monkeypatch.setattr(main, "VectorStore", Mock())
    async with main.lifespan(main.app):
        assert main.app.state.llm is model
        assert main.red_app.state.llm is model
        assert main.blue_app.state.llm is model
    compatible.assert_called_once_with()
    groq.assert_not_called()
    local.assert_not_called()
