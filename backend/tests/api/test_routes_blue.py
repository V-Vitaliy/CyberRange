from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.core.security import get_blue_team_user, get_current_user_secure
from app.db.database import get_db
from app.labs.repository import GameSessionRepository
from app.main import app, blue_app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/me", None),
        ("GET", "/dashboard", None),
        ("GET", "/logs", None),
        ("POST", "/defenses/buy", {"defense_type": "jwt_filter"}),
        ("POST", "/investigate", {"log_id": str(uuid4()), "is_malicious": False}),
    ],
)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/dashboard", "/logs"])
async def test_blue_reads_do_not_expose_another_lab(client, monkeypatch, path):
    user = SimpleNamespace(role="blue_team", id=uuid4(), lab_instance_id=uuid4())
    monkeypatch.setattr(
        GameSessionRepository, "get_by_id", AsyncMock(return_value=SimpleNamespace(lab_instance_id=uuid4()))
    )

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


@pytest.mark.asyncio
@pytest.mark.parametrize("has_session", [False, True])
async def test_profile_resolves_authenticated_lab_session(monkeypatch, has_session):
    user = SimpleNamespace(username="blue", role="blue_team", lab_instance_id=uuid4())
    session_id = uuid4()
    lookup = AsyncMock(return_value=SimpleNamespace(id=session_id) if has_session else None)
    monkeypatch.setattr(GameSessionRepository, "get_by_lab_instance_id", lookup)
    db = object()

    async def current_user():
        return user

    async def database():
        yield db

    blue_app.dependency_overrides.update({get_blue_team_user: current_user, get_db: database})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/blue/me")
    finally:
        blue_app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["session_id"] == (str(session_id) if has_session else None)
    lookup.assert_awaited_once_with(db, user.lab_instance_id)
