import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.ctf import service
from app.ctf.repository import CtfRepository
from app.labs.repository import GameSessionRepository


def test_flag_hash_is_sha256_hex():
    assert service.hash_ctf_flag("test-flag") == hashlib.sha256(b"test-flag").hexdigest()


@pytest.fixture
def lookups(monkeypatch):
    flag_lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(CtfRepository, "get_flag_by_value", flag_lookup)
    monkeypatch.setattr(service, "log_security_event", AsyncMock())
    return flag_lookup


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["id", "lab_instance_id"])
async def test_submission_without_identity_is_unauthorized(lookups, missing):
    user = {"id": str(uuid4()), "lab_instance_id": str(uuid4())}
    del user[missing]
    with pytest.raises(HTTPException) as exc:
        await service.submit_flag(object(), user, "test-flag")
    assert exc.value.status_code == 401
    lookups.assert_not_awaited()


@pytest.mark.asyncio
async def test_submission_without_game_session_is_not_found(lookups, monkeypatch):
    monkeypatch.setattr(GameSessionRepository, "get_by_lab_instance_id", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await service.submit_flag(object(), {"id": str(uuid4()), "lab_instance_id": str(uuid4())}, "test-flag")
    assert exc.value.status_code == 404
    lookups.assert_not_awaited()


@pytest.mark.asyncio
async def test_submission_looks_up_the_hash_in_the_users_lab(lookups, monkeypatch):
    lab = str(uuid4())
    session = SimpleNamespace(team_id=uuid4())
    monkeypatch.setattr(GameSessionRepository, "get_by_lab_instance_id", AsyncMock(return_value=session))
    db = object()
    with pytest.raises(HTTPException) as exc:
        await service.submit_flag(db, {"id": str(uuid4()), "lab_instance_id": lab}, "test-flag")
    assert exc.value.status_code == 400
    lookups.assert_awaited_once_with(db, service.hash_ctf_flag("test-flag"), lab)
