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
from app.auth.repository import UserRepository
from app.auth.service import create_access_token
from app.ctf import service as ctf_service
from app.ctf.repository import CtfRepository
from app.db.database import get_db
from app.labs import service as labs_service
from app.labs.repository import GameSessionRepository
from app.main import app, blue_app, red_app


@pytest.mark.asyncio
async def test_health_route(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "online"


@pytest.mark.asyncio
async def test_startup_uses_provider_factory_and_cleans_up_soc(monkeypatch):
    model = Mock()
    model.with_structured_output.return_value = RunnableLambda(
        lambda _: AnalysisOutput(is_malicious=False, reasoning="test")
    )
    factory = Mock(return_value=model)
    monkeypatch.setattr(main, "create_llm", factory)
    monkeypatch.setattr(main, "VectorStore", Mock())
    object_store = Mock()
    monkeypatch.setattr(main, "ObjectStore", object_store)
    cancelled = asyncio.Event()

    async def worker(graph, queue):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "run_soc_analyst_loop", worker)
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
        assert main.app.state.llm is model
        assert main.red_app.state.llm is model
        assert main.blue_app.state.llm is model
        assert main.red_app.state.object_store is object_store.from_settings.return_value
    assert cancelled.is_set()
    factory.assert_called_once_with()


@pytest.mark.asyncio
async def test_startup_prepares_the_login_dummy_hash_off_the_event_loop(monkeypatch):
    """Login Hardening is constant-time from the first request: no cold dummy hash to build."""
    import threading

    from app.auth import service as auth_service

    monkeypatch.setattr(main, "create_llm", Mock())
    monkeypatch.setattr(main, "create_soc_graph", Mock(side_effect=NotImplementedError))  # no SOC task
    monkeypatch.setattr(main, "VectorStore", Mock())
    monkeypatch.setattr(main, "ObjectStore", Mock())
    threads = []
    real_hash = auth_service.get_password_hash

    def get_password_hash(password):
        threads.append(threading.get_ident())
        return real_hash(password)

    monkeypatch.setattr(auth_service, "get_password_hash", get_password_hash)
    auth_service.dummy_password_hash.cache_clear()
    try:
        async with main.lifespan(main.app):
            assert len(threads) == 1 and threads[0] != threading.get_ident()
            dummy = auth_service.dummy_password_hash()
            assert len(threads) == 1  # served from the cache, not built on the first login
            assert auth_service.verify_password("guess", dummy) is False
    finally:
        auth_service.dummy_password_hash.cache_clear()


@pytest.mark.asyncio
async def test_unsupported_local_soc_does_not_prevent_local_api_startup(monkeypatch):
    model = Mock()
    model.with_structured_output.side_effect = NotImplementedError("no structured output")
    monkeypatch.setattr(main, "create_llm", Mock(return_value=model))
    monkeypatch.setattr(main, "VectorStore", Mock())
    monkeypatch.setattr(main, "ObjectStore", Mock())
    worker = AsyncMock()
    monkeypatch.setattr(main, "run_soc_analyst_loop", worker)
    async with main.lifespan(main.app):
        assert main.app.state.llm is model
    worker.assert_not_called()


@pytest.mark.parametrize(
    "sub_app,path,method",
    [
        (red_app, "/ctf/submit", "post"),
        (red_app, "/me", "get"),
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
    monkeypatch.setattr(ctf_service, "log_security_event", AsyncMock())
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
    vector_store, object_store = Mock(), Mock()
    monkeypatch.setattr(main, "VectorStore", vector_store)
    monkeypatch.setattr(main, "ObjectStore", object_store)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        async with main.lifespan(main.app):
            pass
    vector_store.assert_not_called()
    object_store.from_settings.assert_not_called()


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_before_routing(client, monkeypatch):
    monkeypatch.setattr(main.settings, "UPLOAD_MAX_BYTES", 1)
    response = await client.post("/api/red/upload", content=b"x" * (main.MULTIPART_OVERHEAD_BYTES + 2))
    assert response.status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin,allowed",
    [("http://localhost:3000", True), ("http://127.0.0.1:3000", True), ("http://evil.example", False)],
)
async def test_cors_allows_only_configured_frontend_origins(client, origin, allowed):
    preflight = await client.options(
        "/api/blue/login",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    simple = await client.get("/api/health", headers={"Origin": origin})
    for response in (preflight, simple):
        assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["red", "blue"])
async def test_login_password_check_runs_off_the_event_loop(monkeypatch, side):
    import threading

    from app.auth import router as auth_router
    from app.auth import service as auth_service

    user = SimpleNamespace(
        id=uuid4(), username="test-user", role=side + "_team", lab_instance_id=uuid4(), password_hash="hash"
    )
    monkeypatch.setattr(UserRepository, "get_by_username_in_lab", AsyncMock(return_value=user), raising=False)
    session = SimpleNamespace(lab_instance_id=user.lab_instance_id, login_hardening_enabled=False)
    monkeypatch.setattr(GameSessionRepository, "get_by_lab_instance_id", AsyncMock(return_value=session))
    threads = []

    def verify(plain, hashed):
        threads.append(threading.get_ident())
        return True

    monkeypatch.setattr(auth_service, "verify_password", verify)
    monkeypatch.setattr(auth_router, "log_security_event", AsyncMock())
    sub_app = red_app if side == "red" else blue_app

    async def database():
        yield object()

    previous = dict(sub_app.dependency_overrides)
    sub_app.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            body = {
                "username": "test-user",
                "password": "x",
                "lab_code": labs_service.lab_login_code(user.lab_instance_id),
            }
            response = await client.post(f"/api/{side}/login", json=body)
    finally:
        sub_app.dependency_overrides.clear()
        sub_app.dependency_overrides.update(previous)
    assert response.status_code == 200
    assert threads and threads[0] != threading.get_ident()


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["red", "blue"])
async def test_login_requires_a_lab_code(client, side):
    response = await client.post(f"/api/{side}/login", json={"username": "test-user", "password": "x"})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["red", "blue"])
@pytest.mark.parametrize("code", ["raw-uuid", "", "not-a-code"])
async def test_login_rejects_codes_not_issued_by_the_platform(client, monkeypatch, side, code):
    """A lab UUID is not a credential: a caller who learned another lab's ID cannot probe it."""
    from app.auth import router as auth_router

    authenticate = AsyncMock(return_value=None)
    monkeypatch.setattr(auth_router, "authenticate", authenticate)
    body = {"username": "test-user", "password": "x", "lab_code": str(uuid4()) if code == "raw-uuid" else code}
    response = await client.post(f"/api/{side}/login", json=body)
    assert response.status_code == 401
    assert response.json()["detail"] in ("Invalid credentials", "Incorrect username or password")
    authenticate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["red", "blue"])
async def test_login_passes_lab_source_and_portal_to_authentication(client, monkeypatch, side):
    from app.auth import router as auth_router

    authenticate = AsyncMock(return_value=None)
    monkeypatch.setattr(auth_router, "authenticate", authenticate)
    sub_app = red_app if side == "red" else blue_app

    async def database():
        yield object()

    previous = dict(sub_app.dependency_overrides)
    sub_app.dependency_overrides[get_db] = database
    try:
        lab = uuid4()
        body = {"username": "test-user", "password": "x", "lab_code": labs_service.lab_login_code(lab)}
        response = await client.post(f"/api/{side}/login", json=body)
    finally:
        sub_app.dependency_overrides.clear()
        sub_app.dependency_overrides.update(previous)
    assert response.status_code == 401
    authenticate.assert_awaited_once()
    assert authenticate.call_args.args[1:] == ("test-user", "x", lab)
    assert authenticate.call_args.kwargs == {"source_ip": "127.0.0.1", "portal": side}
