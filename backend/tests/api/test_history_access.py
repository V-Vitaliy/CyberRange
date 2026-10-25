"""Regression: GET /api/red/chat/history must reject unauthorized sessions
before any history search or security logging runs.

Mocked HTTP evidence only: the database, search repository, and security
logging are doubles. These tests do not verify PostgreSQL isolation.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from app.api import routes_red
from app.core.security import get_current_user_vulnerable
from app.db.database import get_db
from app.db.repository import ChatRepository, GameSessionRepository
from app.main import app, red_app


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client():
    # ASGITransport does not run lifespan, load models or start services.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _authenticated_user(lab_instance_id):
    return {
        "sub": "red-student",
        "id": str(uuid4()),
        "role": "red_team",
        "lab_instance_id": str(lab_instance_id),
    }


def _database():
    """Synthetic session double; execute stays an assertable AsyncMock."""
    return SimpleNamespace(execute=AsyncMock())


def _install_rejecting_search_spy(monkeypatch):
    # The vulnerable search is not implemented yet; install a raising spy so a
    # call before authorization is unmistakable (called, not merely awaited).
    spy = AsyncMock(side_effect=AssertionError("chat history search ran before authorization"))
    monkeypatch.setattr(ChatRepository, "search_chat_history_vulnerable", spy, raising=False)
    return spy


def _install_log_spy(monkeypatch):
    log_event = AsyncMock()
    monkeypatch.setattr(routes_red, "log_security_event", log_event)
    return log_event


async def _request_history(client, user, session_id):
    # Override only this route's dependencies on red_app and restore any
    # pre-existing overrides afterwards.
    overrides = red_app.dependency_overrides
    prior = dict(overrides)
    db = _database()

    async def current_user():
        return user

    async def database():
        yield db

    overrides[get_current_user_vulnerable] = current_user
    overrides[get_db] = database
    try:
        response = await client.get(
            "/api/red/chat/history", params={"q": "history", "session_id": session_id}
        )
    finally:
        overrides.clear()
        overrides.update(prior)
    return response, db


async def test_history_foreign_lab_session_rejected_before_search(client, monkeypatch):
    user = _authenticated_user(uuid4())
    foreign_session = SimpleNamespace(lab_instance_id=uuid4())
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=foreign_session))
    search_spy = _install_rejecting_search_spy(monkeypatch)
    log_event = _install_log_spy(monkeypatch)

    response, _db = await _request_history(client, user, str(uuid4()))

    assert response.status_code == 404
    search_spy.assert_not_called()
    log_event.assert_not_called()


async def test_history_absent_session_rejected_before_search(client, monkeypatch):
    user = _authenticated_user(uuid4())
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=None))
    search_spy = _install_rejecting_search_spy(monkeypatch)
    log_event = _install_log_spy(monkeypatch)

    response, _db = await _request_history(client, user, str(uuid4()))

    assert response.status_code == 404
    search_spy.assert_not_called()
    log_event.assert_not_called()


async def test_history_malformed_session_id_rejected_before_search(client, monkeypatch):
    user = _authenticated_user(uuid4())
    # Real repository path: ensure_uuid raises before db.execute is touched.
    search_spy = _install_rejecting_search_spy(monkeypatch)
    log_event = _install_log_spy(monkeypatch)

    response, db = await _request_history(client, user, "not-a-uuid")

    assert response.status_code == 422
    db.execute.assert_not_called()
    search_spy.assert_not_called()
    log_event.assert_not_called()
