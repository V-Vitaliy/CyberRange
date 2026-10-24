from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.api import routes_blue
from app.core.security import get_blue_team_user
from app.db.database import get_db
from app.main import app, blue_app


@pytest.mark.asyncio
@pytest.mark.parametrize('has_session', [False, True])
async def test_profile_resolves_authenticated_lab_session(monkeypatch, has_session):
    user = SimpleNamespace(username='blue', role='blue_team', lab_instance_id=uuid4())
    session_id = uuid4()
    lookup = AsyncMock(return_value=SimpleNamespace(id=session_id) if has_session else None)
    monkeypatch.setattr(routes_blue.GameSessionRepository, 'get_by_lab_instance_id', lookup)
    db = object()

    async def current_user():
        return user

    async def database():
        yield db

    blue_app.dependency_overrides.update({get_blue_team_user: current_user, get_db: database})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/api/blue/me')
    finally:
        blue_app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()['session_id'] == (str(session_id) if has_session else None)
    lookup.assert_awaited_once_with(db, user.lab_instance_id)
