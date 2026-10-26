"""OpenAPI auth declarations plus real auth dependencies over mocked storage."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.core.security import create_access_token
from app.db.database import get_db
from app.db.repository import CtfRepository, GameSessionRepository, UserRepository
from app.api import routes_ctf
from app.main import app, blue_app, red_app


@pytest.mark.parametrize('sub_app,path,method', [
    (red_app, '/ctf/submit', 'post'),
    (blue_app, '/me', 'get'),
])
def test_swagger_declares_http_bearer_instead_of_reserved_header(sub_app, path, method):
    schema = sub_app.openapi()
    scheme = schema['components']['securitySchemes']['BearerAuth']
    assert scheme['type'] == 'http'
    assert scheme['scheme'] == 'bearer'
    assert scheme['bearerFormat'] == 'JWT'
    operation = schema['paths'][path][method]
    assert operation['security'] == [{'BearerAuth': []}]
    assert not any(p['name'].lower() == 'authorization' for p in operation.get('parameters', []))
    assert not schema['paths']['/login']['post'].get('security')


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['red', 'blue'])
async def test_bearer_header_reaches_existing_auth_checks(monkeypatch, side):
    user = SimpleNamespace(id=uuid4(), username='test-user', role=side + '_team', lab_instance_id=uuid4())
    session = SimpleNamespace(id=uuid4(), team_id=uuid4(), lab_instance_id=user.lab_instance_id, jwt_filter_enabled=False)
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(UserRepository, 'get_by_id', lookup)
    monkeypatch.setattr(UserRepository, 'get_by_username', lookup)
    monkeypatch.setattr(GameSessionRepository, 'get_by_lab_instance_id', AsyncMock(return_value=session))
    flag_lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(CtfRepository, 'get_flag_by_value', flag_lookup)
    monkeypatch.setattr(routes_ctf, 'log_security_event', AsyncMock())
    token = create_access_token(dict(sub=user.username, role=user.role,
                                     id=str(user.id), lab_instance_id=str(user.lab_instance_id)))

    async def database():
        yield object()

    sub_app = red_app if side == 'red' else blue_app
    previous = dict(sub_app.dependency_overrides)
    sub_app.dependency_overrides[get_db] = database
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            async def request(headers):
                if side == 'red':
                    return await client.post('/api/red/ctf/submit', headers=headers, json={'flag_value': 'unregistered-test-value'})
                return await client.get('/api/blue/me', headers=headers)

            for headers in ({}, {'Authorization': token}):
                response = await request(headers)
                assert response.status_code == 401
                lookup.assert_not_awaited()

            response = await request({'Authorization': 'Bearer ' + token})
            lookup.assert_awaited_once()
            if side == 'red':
                # Auth passed; an unknown flag is an application-level 400.
                assert response.status_code == 400
                assert response.json()['detail'] == 'Invalid flag.'
                assert flag_lookup.call_args.args[2] == str(user.lab_instance_id)
            else:
                assert response.status_code == 200
                assert response.json()['session_id'] == str(session.id)
    finally:
        sub_app.dependency_overrides.clear()
        sub_app.dependency_overrides.update(previous)
