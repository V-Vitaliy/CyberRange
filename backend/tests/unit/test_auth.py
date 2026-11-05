from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException

from app.auth import service
from app.auth.repository import UserRepository
from app.labs.repository import GameSessionRepository


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(service, "SECRET_KEY", "isolated-test-secret-with-at-least-32-bytes")
    user = SimpleNamespace(id=uuid4(), username="student", role="red_team", lab_instance_id=uuid4())
    session = SimpleNamespace(jwt_filter_enabled=False, lab_instance_id=user.lab_instance_id)
    monkeypatch.setattr(UserRepository, "get_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(GameSessionRepository, "get_by_lab_instance_id", AsyncMock(return_value=session))
    claims = dict(sub=user.username, id=str(user.id), role=user.role, lab_instance_id=str(user.lab_instance_id))
    return user, session, claims


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_genuine_token_works_in_both_modes(identity, protected):
    user, session, claims = identity
    session.jwt_filter_enabled = protected
    token = service.create_access_token(claims)
    result = await service.get_current_user_vulnerable(f"Bearer {token}", object())
    assert result["id"] == str(user.id)
    assert result["role"] == "red_team"


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("algorithm", ["none", "HS256"])
async def test_role_forgery_only_works_without_defense(identity, protected, algorithm):
    _, session, claims = identity
    session.jwt_filter_enabled = protected
    payload = jwt.decode(service.create_access_token(claims), options={"verify_signature": False})
    payload["role"] = "admin"
    token = jwt.encode(
        payload, None if algorithm == "none" else "attacker-test-key-with-at-least-32-bytes", algorithm=algorithm
    )
    if protected:
        with pytest.raises(HTTPException) as exc:
            await service.get_current_user_vulnerable(f"Bearer {token}", object())
        assert exc.value.status_code == 401
    else:
        result = await service.get_current_user_vulnerable(f"Bearer {token}", object())
        assert result["role"] == "admin"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value", [("id", str(uuid4())), ("lab_instance_id", str(uuid4())), ("sub", "another-user")]
)
async def test_forged_identity_cannot_escape_lab_scope(identity, key, value):
    _, _, claims = identity
    payload = jwt.decode(service.create_access_token(claims), options={"verify_signature": False})
    payload[key] = value
    token = jwt.encode(payload, None, algorithm="none")
    with pytest.raises(HTTPException) as exc:
        await service.get_current_user_vulnerable(f"Bearer {token}", object())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_lab_scope_cannot_be_forged(identity):
    _, _, claims = identity
    payload = jwt.decode(service.create_access_token(claims), options={"verify_signature": False})
    scope = jwt.decode(payload["lab_scope"], options={"verify_signature": False})
    payload["lab_scope"] = jwt.encode(scope, None, algorithm="none")
    with pytest.raises(HTTPException):
        await service.get_current_user_vulnerable(f"Bearer {jwt.encode(payload, None, algorithm='none')}", object())


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_expired_lab_scope_is_rejected(identity, protected):
    _, session, claims = identity
    session.jwt_filter_enabled = protected
    token = service.create_access_token(claims, timedelta(seconds=-1))
    with pytest.raises(HTTPException):
        await service.get_current_user_vulnerable(f"Bearer {token}", object())


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["blue_team", "admin", "red_team"])
async def test_blue_api_role_guard(role):
    user = SimpleNamespace(role=role)
    if role == "red_team":
        with pytest.raises(HTTPException) as exc:
            await service.get_blue_team_user(user)
        assert exc.value.status_code == 403
    else:
        assert await service.get_blue_team_user(user) is user


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["malformed", jwt.encode({"id": str(uuid4())}, None, algorithm="none")])
async def test_malformed_or_unscoped_token_requires_relogin(identity, token):
    with pytest.raises(HTTPException) as exc:
        await service.get_current_user_vulnerable(f"Bearer {token}", object())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_buying_jwt_defense_blocks_previously_accepted_forgery(identity, monkeypatch):
    from app.defense import service as defense

    _, session, claims = identity
    session.defense_budget = 5
    session.id = uuid4()
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=session))
    genuine = service.create_access_token(claims)
    payload = jwt.decode(genuine, options={"verify_signature": False})
    payload["role"] = "admin"
    forged = jwt.encode(payload, None, algorithm="none")
    assert (await service.get_current_user_vulnerable(f"Bearer {forged}", object()))["role"] == "admin"
    # Other flags are needed to serialize the existing purchase response.
    session.system_prompt = "You are a helpful university assistant."
    session.rate_limit_enabled = False
    session.use_reranker = False
    await defense.process_defense_purchase(SimpleNamespace(commit=AsyncMock()), session.id, "jwt_filter")
    with pytest.raises(HTTPException):
        await service.get_current_user_vulnerable(f"Bearer {forged}", object())
    assert (await service.get_current_user_vulnerable(f"Bearer {genuine}", object()))["role"] == "red_team"


@pytest.fixture
def stored_user(monkeypatch):
    user = SimpleNamespace(username="student", password_hash=service.get_password_hash("correct-password"))
    lookup = AsyncMock(side_effect=lambda db, username: user if username == user.username else None)
    monkeypatch.setattr(UserRepository, "get_by_username", lookup)
    return user


@pytest.mark.asyncio
async def test_authenticate_returns_user_for_correct_password(stored_user):
    assert await service.authenticate(object(), "student", "correct-password") is stored_user


@pytest.mark.asyncio
@pytest.mark.parametrize("username,password", [("student", "wrong-password"), ("nobody", "correct-password")])
async def test_authenticate_rejects_wrong_password_or_unknown_user(stored_user, username, password):
    assert await service.authenticate(object(), username, password) is None


def test_auth_routers_hold_login_and_blue_profile():
    from app.auth.router import blue_router, red_router

    assert [(route.path, route.methods) for route in red_router.routes] == [("/login", {"POST"})]
    assert [(route.path, route.methods) for route in blue_router.routes] == [("/login", {"POST"}), ("/me", {"GET"})]
