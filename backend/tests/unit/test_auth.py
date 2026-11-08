from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock
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
    # The SIEM writer is replaced for every identity test; forgery_log exposes it.
    monkeypatch.setattr(service, "log_jwt_manipulation_attempt", AsyncMock(), raising=False)
    claims = dict(sub=user.username, id=str(user.id), role=user.role, lab_instance_id=str(user.lab_instance_id))
    return user, session, claims


@pytest.fixture
def forgery_log(identity):
    return service.log_jwt_manipulation_attempt


def forge_role(token, role="admin"):
    payload = jwt.decode(token, options={"verify_signature": False})
    payload["role"] = role
    return jwt.encode(payload, None, algorithm="none")


CLIENT = SimpleNamespace(client=SimpleNamespace(host="203.0.113.9"))


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
    session.login_hardening_enabled = False
    await defense.process_defense_purchase(SimpleNamespace(commit=AsyncMock()), session.id, "jwt_filter")
    with pytest.raises(HTTPException):
        await service.get_current_user_vulnerable(f"Bearer {forged}", object())
    assert (await service.get_current_user_vulnerable(f"Bearer {genuine}", object()))["role"] == "red_team"


LAB, OTHER_LAB = uuid4(), uuid4()
STORED_HASH = service.get_password_hash("correct-password")


@pytest.fixture
def login_lab(monkeypatch):
    """Two labs; only LAB has the user "student". Password checks and SIEM writes are recorded."""
    user = SimpleNamespace(username="student", lab_instance_id=LAB, password_hash=STORED_HASH)
    sessions = {lab: SimpleNamespace(lab_instance_id=lab, login_hardening_enabled=False) for lab in (LAB, OTHER_LAB)}
    users = AsyncMock(side_effect=lambda db, name, lab: user if (name, lab) == ("student", LAB) else None)
    monkeypatch.setattr(UserRepository, "get_by_username_in_lab", users, raising=False)
    monkeypatch.setattr(
        GameSessionRepository, "get_by_lab_instance_id", AsyncMock(side_effect=lambda db, lab: sessions.get(lab))
    )
    checked = []
    real_verify = service.verify_password

    def verify(plain, hashed):
        checked.append(hashed)
        return real_verify(plain, hashed)

    monkeypatch.setattr(service, "verify_password", verify)
    monkeypatch.setattr(service, "log_failed_login", AsyncMock(), raising=False)
    monkeypatch.setattr(service, "enforce_login_attempt_limit", AsyncMock(), raising=False)
    return SimpleNamespace(
        user=user,
        session=sessions[LAB],
        users=users,
        checked=checked,
        failed=service.log_failed_login,
        limit=service.enforce_login_attempt_limit,
    )


async def login(username="student", password="correct-password", lab=LAB, db=None):
    db = db or SimpleNamespace(rollback=AsyncMock())
    return await service.authenticate(db, username, password, lab, source_ip="203.0.113.9", portal="red")


@pytest.mark.asyncio
@pytest.mark.parametrize("username,password", [("student", "wrong-password"), ("nobody", "correct-password")])
async def test_failed_login_releases_the_request_connection_before_logging(login_lab, username, password):
    """The event write needs its own connection: holding both lets a burst of failures starve the pool."""
    db = SimpleNamespace(rollback=AsyncMock())

    async def logged(**event):
        assert db.rollback.await_count == 1, "the request transaction is still open"

    login_lab.failed.side_effect = logged
    assert await login(username, password, db=db) is None
    login_lab.failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_login_keeps_the_request_transaction(login_lab):
    db = SimpleNamespace(rollback=AsyncMock())
    assert await login(db=db) is login_lab.user
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_returns_the_user_of_the_requested_lab(login_lab):
    assert await login() is login_lab.user
    login_lab.users.assert_awaited_once_with(ANY, "student", LAB)
    login_lab.failed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "username,password,lab",
    [
        ("student", "wrong-password", LAB),
        ("nobody", "correct-password", LAB),
        # Usernames are looked up inside the requested lab only.
        ("student", "correct-password", OTHER_LAB),
    ],
)
async def test_failed_login_is_logged_in_the_requested_lab_without_the_password(login_lab, username, password, lab):
    assert await login(username, password, lab) is None
    # No reason is recorded: it would tell whether the username exists.
    login_lab.failed.assert_awaited_once_with(
        lab_instance_id=str(lab), username=username, source_ip="203.0.113.9", portal="red"
    )
    assert password not in repr(login_lab.failed.call_args)


@pytest.mark.asyncio
async def test_unknown_lab_fails_without_event_or_password_check(login_lab):
    assert await login(lab=uuid4()) is None
    login_lab.failed.assert_not_awaited()
    login_lab.limit.assert_not_awaited()
    assert login_lab.checked == []


@pytest.mark.asyncio
async def test_without_hardening_only_known_users_reach_bcrypt(login_lab):
    """The EX-07 timing difference stays until the Blue Team buys Login Hardening."""
    await login("nobody")
    assert login_lab.checked == []
    await login("student", "wrong-password")
    assert login_lab.checked == [STORED_HASH]
    login_lab.limit.assert_not_awaited()


@pytest.mark.asyncio
async def test_hardening_checks_unknown_users_against_a_dummy_hash(login_lab):
    login_lab.session.login_hardening_enabled = True
    assert await login("nobody") is None
    assert login_lab.checked == [service.dummy_password_hash()]
    login_lab.failed.assert_awaited_once()
    assert await login() is login_lab.user
    assert login_lab.checked[-1] == STORED_HASH


def test_dummy_hash_costs_as_much_as_a_real_hash():
    dummy = service.dummy_password_hash()
    assert dummy.split("$")[2] == STORED_HASH.split("$")[2]  # bcrypt cost factor
    assert service.dummy_password_hash() == dummy  # computed once, not per request


@pytest.mark.asyncio
async def test_hardening_limits_attempts_before_any_lookup(login_lab):
    login_lab.session.login_hardening_enabled = True
    login_lab.limit.side_effect = HTTPException(status_code=429, detail="Too many login attempts")
    with pytest.raises(HTTPException) as exc:
        await login()
    assert exc.value.status_code == 429
    login_lab.limit.assert_awaited_once_with(service.redis_client, LAB, "203.0.113.9")
    login_lab.users.assert_not_awaited()
    login_lab.failed.assert_not_awaited()
    assert login_lab.checked == []


@pytest.mark.asyncio
async def test_hardened_login_releases_the_request_connection_before_the_limiter(login_lab):
    """The limiter writes LOGIN_RATE_LIMITED on its own connection when it blocks a source."""
    login_lab.session.login_hardening_enabled = True
    db = SimpleNamespace(rollback=AsyncMock())

    async def limit(redis, lab, source_ip):
        assert db.rollback.await_count == 1, "the request transaction is still open"

    login_lab.limit.side_effect = limit
    assert await login(db=db) is login_lab.user
    login_lab.limit.assert_awaited_once()


def test_auth_routers_hold_login_and_profile():
    from app.auth.router import blue_router, red_router

    assert [(route.path, route.methods) for route in red_router.routes] == [("/login", {"POST"}), ("/me", {"GET"})]
    assert [(route.path, route.methods) for route in blue_router.routes] == [("/login", {"POST"}), ("/me", {"GET"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_role_forgery_is_logged_with_claimed_role_and_source_ip(identity, forgery_log, protected):
    user, session, claims = identity
    session.jwt_filter_enabled = protected
    forged = forge_role(service.create_access_token(claims))
    db = object()
    if protected:
        with pytest.raises(HTTPException) as exc:
            await service.get_current_user_vulnerable(f"Bearer {forged}", db, CLIENT)
        assert exc.value.status_code == 401
    else:
        assert (await service.get_current_user_vulnerable(f"Bearer {forged}", db, CLIENT))["role"] == "admin"
    forgery_log.assert_awaited_once_with(
        lab_instance_id=str(user.lab_instance_id),
        provided_role="admin",
        source_ip="203.0.113.9",
        user_id=str(user.id),
        blocked=protected,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [False, True])
async def test_genuine_token_logs_no_forgery(identity, forgery_log, protected):
    _, session, claims = identity
    session.jwt_filter_enabled = protected
    await service.get_current_user_vulnerable(f"Bearer {service.create_access_token(claims)}", object(), CLIENT)
    forgery_log.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [("sub", "another-user"), ("lab_instance_id", str(uuid4()))])
async def test_rejected_identity_forgery_writes_no_role_event(identity, forgery_log, key, value):
    _, _, claims = identity
    payload = jwt.decode(service.create_access_token(claims), options={"verify_signature": False})
    payload.update({key: value, "role": "admin"})
    with pytest.raises(HTTPException):
        await service.get_current_user_vulnerable(f"Bearer {jwt.encode(payload, None, algorithm='none')}", object())
    forgery_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_forgery_without_connection_logs_unknown_source(identity, forgery_log):
    user, _, claims = identity
    forged = forge_role(service.create_access_token(claims))
    await service.get_current_user_vulnerable(f"Bearer {forged}", object())
    assert forgery_log.await_args.kwargs["source_ip"] == "unknown"


@pytest.mark.asyncio
async def test_failed_audit_write_leaves_the_request_session_alone(identity, monkeypatch):
    from app.siem import service as siem
    from tests.unit.test_siem import AuditSessions, FakeRedis

    _, _, claims = identity
    monkeypatch.setattr(service, "log_jwt_manipulation_attempt", siem.log_jwt_manipulation_attempt)
    audit = AuditSessions(commit_errors=[True])
    monkeypatch.setattr(siem, "siem_session", audit, raising=False)
    monkeypatch.setattr(siem, "redis_client", FakeRedis())
    request_db = SimpleNamespace(add=Mock(), commit=AsyncMock(), rollback=AsyncMock())
    forged = forge_role(service.create_access_token(claims))
    result = await service.get_current_user_vulnerable(f"Bearer {forged}", request_db, CLIENT)
    assert result["role"] == "admin"
    assert audit.rollbacks == 1
    request_db.add.assert_not_called()
    request_db.commit.assert_not_awaited()
    request_db.rollback.assert_not_awaited()
