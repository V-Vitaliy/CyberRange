from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.db.models import ChatThread
from app.db.repository import ChatRepository


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_user,foreign_session", [(True, False), (False, True)])
async def test_existing_thread_requires_owner_and_session(foreign_user, foreign_session):
    user_id, session_id = uuid4(), uuid4()
    thread = ChatThread(
        id=uuid4(), user_id=uuid4() if foreign_user else user_id, session_id=uuid4() if foreign_session else session_id
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=Mock(scalars=Mock(return_value=Mock(first=Mock(return_value=thread))))),
        commit=AsyncMock(),
    )
    with pytest.raises(HTTPException) as exc:
        await ChatRepository.get_or_create_thread(db, thread.id, user_id, session_id, "hello")
    assert exc.value.status_code == 404
    db.commit.assert_not_awaited()
