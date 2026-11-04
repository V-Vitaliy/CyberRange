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
