from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.labs import service
from app.labs.repository import GameSessionRepository


@pytest.fixture
def stored(monkeypatch):
    lab = uuid4()
    session = SimpleNamespace(id=uuid4(), lab_instance_id=lab)
    by_id = AsyncMock(return_value=session)
    by_lab = AsyncMock(return_value=session)
    monkeypatch.setattr(GameSessionRepository, "get_by_id", by_id)
    monkeypatch.setattr(GameSessionRepository, "get_by_lab_instance_id", by_lab)
    return SimpleNamespace(lab=lab, session=session, by_id=by_id, by_lab=by_lab)


@pytest.mark.asyncio
@pytest.mark.parametrize("lab_as", [str, lambda lab: lab], ids=["string", "uuid"])
async def test_authorized_session_is_returned_for_its_own_lab(stored, lab_as):
    db = object()
    assert await service.get_authorized_session(db, stored.session.id, lab_as(stored.lab)) is stored.session
    stored.by_id.assert_awaited_once_with(db, stored.session.id)


@pytest.mark.asyncio
async def test_session_from_another_lab_is_not_available(stored):
    with pytest.raises(HTTPException) as exc:
        await service.get_authorized_session(object(), stored.session.id, uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_missing_session_is_not_available(stored):
    stored.by_id.return_value = None
    with pytest.raises(HTTPException) as exc:
        await service.get_authorized_session(object(), uuid4(), stored.lab)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_malformed_lab_id_is_rejected(stored):
    with pytest.raises(HTTPException) as exc:
        await service.get_authorized_session(object(), stored.session.id, "not-a-uuid")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("for_update", [False, True])
async def test_get_session_passes_row_lock_choice_to_repository(stored, for_update):
    db = object()
    assert await service.get_session(db, stored.session.id, for_update=for_update) is stored.session
    stored.by_id.assert_awaited_once_with(db, stored.session.id, for_update=for_update)


@pytest.mark.asyncio
async def test_get_session_for_lab_looks_up_by_lab(stored):
    db = object()
    assert await service.get_session_for_lab(db, stored.lab) is stored.session
    stored.by_lab.assert_awaited_once_with(db, stored.lab)


def test_lab_login_code_names_its_lab():
    lab = uuid4()
    code = service.lab_login_code(lab)
    assert service.lab_from_login_code(code) == lab
    assert str(lab) not in code and lab.hex not in code


@pytest.mark.parametrize(
    "forge",
    [
        # Lab UUIDs are not secret (the SQLi exercise can list every lab's database role).
        lambda lab, other: str(other),
        lambda lab, other: other.hex,
        # A genuine code with its lab part swapped for another lab.
        lambda lab, other: service.lab_login_code(other)[:22] + service.lab_login_code(lab)[22:],
        lambda lab, other: service.lab_login_code(lab)[:-1],
        lambda lab, other: "",
        lambda lab, other: "not a code",
        lambda lab, other: "é" * 43,
    ],
)
def test_forged_or_malformed_login_codes_name_no_lab(forge):
    lab, other = uuid4(), uuid4()
    assert service.lab_from_login_code(forge(lab, other)) is None


def test_login_codes_depend_on_the_platform_secret(monkeypatch):
    lab = uuid4()
    code = service.lab_login_code(lab)
    monkeypatch.setattr(service.settings, "JWT_SECRET", "another-test-secret-with-at-least-32-bytes")
    assert service.lab_from_login_code(code) is None
