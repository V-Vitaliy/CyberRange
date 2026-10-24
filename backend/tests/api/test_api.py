from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
import pytest_asyncio

from app.core.security import get_current_user_secure
from app.db.database import get_db
from app.db.repository import ChatRepository, GameSessionRepository
from app.main import app, blue_app


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    # ASGITransport does not run lifespan, load models or start services.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_health_route(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "online"


async def test_history_route_requires_authentication(client):
    async def database():
        yield object()

    from app.main import red_app
    red_app.dependency_overrides[get_db] = database
    try:
        response = await client.get("/api/red/chat/history", params={"q": "hello", "session_id": str(uuid4())})
    finally:
        red_app.dependency_overrides.clear()
    assert response.status_code == 401


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/me", None),
    ("GET", "/dashboard", None),
    ("GET", "/logs", None),
    ("POST", "/defenses/buy", {"defense_type": "jwt_filter"}),
    ("POST", "/investigate", {"log_id": str(uuid4()), "is_malicious": False}),
])
async def test_blue_routes_reject_signed_red_team_identity(client, method, path, body):
    user = SimpleNamespace(role="red_team", id=uuid4(), lab_instance_id=uuid4())

    async def database():
        yield object()

    async def current_user():
        return user

    blue_app.dependency_overrides[get_current_user_secure] = current_user
    blue_app.dependency_overrides[get_db] = database
    session_id = str(uuid4())
    kwargs = {"params": {"session_id": session_id}} if body is None else {"json": dict(body, session_id=session_id)}
    try:
        response = await client.request(method, f"/api/blue{path}", **kwargs)
    finally:
        blue_app.dependency_overrides.clear()
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/dashboard", "/logs"])
async def test_blue_reads_do_not_expose_another_lab(client, monkeypatch, path):
    user = SimpleNamespace(role="blue_team", id=uuid4(), lab_instance_id=uuid4())
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=SimpleNamespace(lab_instance_id=uuid4())))

    async def database():
        yield object()

    async def current_user():
        return user

    blue_app.dependency_overrides[get_current_user_secure] = current_user
    blue_app.dependency_overrides[get_db] = database
    try:
        response = await client.get(f"/api/blue{path}", params={"session_id": str(uuid4())})
    finally:
        blue_app.dependency_overrides.clear()
    assert response.status_code == 404


@pytest.mark.xfail(
    not hasattr(ChatRepository, "search_chat_history_vulnerable"),
    reason="SQLi repository implementation is missing; safe lab isolation needs a separate decision",
    strict=True,
)
async def test_sqli_repository_scenario_is_available():
    # Keep the known missing scenario visible rather than accepting AttributeError
    # as proof that a SQL injection reached the database.
    assert hasattr(ChatRepository, "search_chat_history_vulnerable")
