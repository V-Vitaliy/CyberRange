"""Mocked API behavior, not evidence of PostgreSQL privilege enforcement."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.api import routes_red
from app.core.security import get_current_user_vulnerable
from app.db.database import get_db
from app.db.repository import ChatRepository, GameSessionRepository
from app.db.sqli_isolation import IsolationError
from app.db.sqli_search import HistoryQueryError
from app.main import app, red_app


@pytest.fixture
def history(monkeypatch):
    lab, session = uuid4(), uuid4()
    user = {'id': str(uuid4()), 'lab_instance_id': str(lab), 'role': 'red_team'}
    db = object()
    async def current_user():
        return user
    async def database():
        yield db
    prior = dict(red_app.dependency_overrides)
    red_app.dependency_overrides.update({get_db: database, get_current_user_vulnerable: current_user})
    monkeypatch.setattr(GameSessionRepository, 'get_by_id', AsyncMock(
        return_value=SimpleNamespace(lab_instance_id=lab)))
    search = AsyncMock(return_value=([{'title': 'normal', 'messages': []}], 'synthetic SQL'))
    monkeypatch.setattr(ChatRepository, 'search_chat_history_vulnerable', search)
    log = AsyncMock()
    monkeypatch.setattr(routes_red, 'log_security_event', log)
    try:
        yield SimpleNamespace(lab=lab, session=session, search=search, log=log, db=db)
    finally:
        red_app.dependency_overrides.clear()
        red_app.dependency_overrides.update(prior)


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ['normal', "' OR TRUE --"])
async def test_history_awaits_lab_search_and_preserves_response(history, query):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/api/red/chat/history', params={'q': query, 'session_id': str(history.session)})
    assert response.status_code == 200
    assert response.json() == {'results': [{'title': 'normal', 'messages': []}]}
    history.search.assert_awaited_once_with(str(history.lab), str(history.session), query)
    history.log.assert_awaited_once()
    event = history.log.call_args.kwargs
    assert event['event_type'] == 'DB_QUERY'
    assert event['payload'] == {'query_param': query, 'target': 'chat_history'}
    assert event['db'] is history.db


@pytest.mark.asyncio
@pytest.mark.parametrize('error,status', [
    (HistoryQueryError('SQL syntax error [42601]'), 400),
    (IsolationError('Restricted lab connection missing.'), 503),
])
async def test_history_logs_error_and_next_request_works(history, error, status):
    history.search.side_effect = [error, ([{'title': 'recovered'}], 'synthetic SQL')]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        failed = await client.get('/api/red/chat/history', params={'q': "'", 'session_id': str(history.session)})
        recovered = await client.get('/api/red/chat/history', params={'q': 'normal', 'session_id': str(history.session)})
    assert failed.status_code == status
    assert failed.json() == {'detail': str(error)}
    assert recovered.status_code == 200
    assert recovered.json() == {'results': [{'title': 'recovered'}]}
    assert [call.kwargs['event_type'] for call in history.log.await_args_list] == [
        'DB_QUERY', 'SQL_ERROR', 'DB_QUERY']
    assert history.log.await_args_list[1].kwargs['payload'] == {'error': str(error), 'failed_query': "'"}
