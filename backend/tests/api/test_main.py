"""Application wiring: health, startup lifecycle and OpenAPI bearer auth."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from langchain_core.runnables import RunnableLambda

from app import main
from app.agents.soc_agent import AnalysisOutput
from app.api import routes_ctf
from app.core.security import create_access_token
from app.db.database import get_db
from app.db.repository import CtfRepository, GameSessionRepository, UserRepository
from app.main import app, blue_app, red_app


@pytest.mark.asyncio
async def test_health_route(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "online"


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud", [False, True])
async def test_startup_uses_selected_model_and_cleans_up_soc(monkeypatch, cloud):
    model = Mock()
    model.with_structured_output.return_value = RunnableLambda(
        lambda _: AnalysisOutput(is_malicious=False, reasoning="test")
    )
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


@pytest.mark.parametrize(
    "sub_app,path,method",
    [
        (red_app, "/ctf/submit", "post"),
        (blue_app, "/me", "get"),
    ],
)
def test_swagger_declares_http_bearer_instead_of_reserved_header(sub_app, path, method):
    schema = sub_app.openapi()
    scheme = schema["components"]["securitySchemes"]["BearerAuth"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert scheme["bearerFormat"] == "JWT"
    operation = schema["paths"][path][method]
    assert operation["security"] == [{"BearerAuth": []}]
    assert not any(p["name"].lower() == "authorization" for p in operation.get("parameters", []))
    assert not schema["paths"]["/login"]["post"].get("security")


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["red", "blue"])
async def test_bearer_header_reaches_existing_auth_checks(monkeypatch, side):
    user = SimpleNamespace(id=uuid4(), username="test-user", role=side + "_team", lab_instance_id=uuid4())
    session = SimpleNamespace(
        id=uuid4(), team_id=uuid4(), lab_instance_id=user.lab_instance_id, jwt_filter_enabled=False
    )
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(UserRepository, "get_by_id", lookup)
    monkeypatch.setattr(UserRepository, "get_by_username", lookup)
    monkeypatch.setattr(GameSessionRepository, "get_by_lab_instance_id", AsyncMock(return_value=session))
    flag_lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(CtfRepository, "get_flag_by_value", flag_lookup)
    monkeypatch.setattr(routes_ctf, "log_security_event", AsyncMock())
    token = create_access_token(
        dict(sub=user.username, role=user.role, id=str(user.id), lab_instance_id=str(user.lab_instance_id))
    )

    async def database():
        yield object()

    sub_app = red_app if side == "red" else blue_app
    previous = dict(sub_app.dependency_overrides)
    sub_app.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:

            async def request(headers):
                if side == "red":
                    return await client.post(
                        "/api/red/ctf/submit", headers=headers, json={"flag_value": "unregistered-test-value"}
                    )
                return await client.get("/api/blue/me", headers=headers)

            for headers in ({}, {"Authorization": token}):
                response = await request(headers)
                assert response.status_code == 401
                lookup.assert_not_awaited()

            response = await request({"Authorization": "Bearer " + token})
            lookup.assert_awaited_once()
            if side == "red":
                # Auth passed; an unknown flag is an application-level 400.
                assert response.status_code == 400
                assert response.json()["detail"] == "Invalid flag."
                assert flag_lookup.call_args.args[2] == str(user.lab_instance_id)
            else:
                assert response.status_code == 200
                assert response.json()["session_id"] == str(session.id)
    finally:
        sub_app.dependency_overrides.clear()
        sub_app.dependency_overrides.update(previous)


@pytest.mark.asyncio
async def test_startup_refuses_weak_jwt_secret_before_loading_models(monkeypatch):
    monkeypatch.setattr(main.settings, "JWT_SECRET", "super-secret-blue-team-key")
    vector_store = Mock()
    monkeypatch.setattr(main, "VectorStore", vector_store)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        async with main.lifespan(main.app):
            pass
    vector_store.assert_not_called()


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_before_routing(client, monkeypatch):
    monkeypatch.setattr(main.settings, "UPLOAD_MAX_BYTES", 1)
    response = await client.post("/api/red/upload", content=b"x" * (main.MULTIPART_OVERHEAD_BYTES + 2))
    assert response.status_code == 413
