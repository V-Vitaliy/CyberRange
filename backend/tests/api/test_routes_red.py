"""Red Team routes with mocked storage; not evidence of PostgreSQL isolation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from app.api import routes_red
from app.auth.service import get_current_user_vulnerable
from app.chat import router as chat_router
from app.chat.repository import ChatRepository
from app.chat.sqli_isolation import IsolationError
from app.chat.sqli_search import HistoryQueryError
from app.core.redis_client import get_redis
from app.db.chroma_client import get_vector_store
from app.db.database import get_db
from app.db.object_store import get_object_store
from app.labs.repository import GameSessionRepository
from app.llm import provider
from app.main import app, red_app


@pytest.mark.asyncio
async def test_history_route_requires_authentication(client):
    async def database():
        yield object()

    from app.main import red_app

    red_app.dependency_overrides[get_db] = database
    try:
        response = await client.get("/api/red/chat/history", params={"q": "hello", "session_id": str(uuid4())})
    finally:
        red_app.dependency_overrides.clear()
    assert response.status_code == 401


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
    monkeypatch.setattr(chat_router, "log_security_event", log_event)
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
        response = await client.get("/api/red/chat/history", params={"q": "history", "session_id": session_id})
    finally:
        overrides.clear()
        overrides.update(prior)
    return response, db


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_history_absent_session_rejected_before_search(client, monkeypatch):
    user = _authenticated_user(uuid4())
    monkeypatch.setattr(GameSessionRepository, "get_by_id", AsyncMock(return_value=None))
    search_spy = _install_rejecting_search_spy(monkeypatch)
    log_event = _install_log_spy(monkeypatch)

    response, _db = await _request_history(client, user, str(uuid4()))

    assert response.status_code == 404
    search_spy.assert_not_called()
    log_event.assert_not_called()


@pytest.mark.asyncio
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


@pytest.fixture
def history(monkeypatch):
    lab, session = uuid4(), uuid4()
    user = {"id": str(uuid4()), "lab_instance_id": str(lab), "role": "red_team"}
    db = object()

    async def current_user():
        return user

    async def database():
        yield db

    prior = dict(red_app.dependency_overrides)
    red_app.dependency_overrides.update({get_db: database, get_current_user_vulnerable: current_user})
    monkeypatch.setattr(
        GameSessionRepository, "get_by_id", AsyncMock(return_value=SimpleNamespace(lab_instance_id=lab))
    )
    search = AsyncMock(return_value=([{"title": "normal", "messages": []}], "synthetic SQL"))
    monkeypatch.setattr(ChatRepository, "search_chat_history_vulnerable", search)
    log = AsyncMock()
    monkeypatch.setattr(chat_router, "log_security_event", log)
    try:
        yield SimpleNamespace(lab=lab, session=session, search=search, log=log, db=db)
    finally:
        red_app.dependency_overrides.clear()
        red_app.dependency_overrides.update(prior)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["normal", "' OR TRUE --"])
async def test_history_awaits_lab_search_and_preserves_response(history, query):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/red/chat/history", params={"q": query, "session_id": str(history.session)})
    assert response.status_code == 200
    assert response.json() == {"results": [{"title": "normal", "messages": []}]}
    history.search.assert_awaited_once_with(str(history.lab), str(history.session), query)
    history.log.assert_awaited_once()
    event = history.log.call_args.kwargs
    assert event["event_type"] == "DB_QUERY"
    assert event["payload"] == {"query_param": query, "target": "chat_history"}
    assert event["db"] is history.db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,status",
    [
        (HistoryQueryError("SQL syntax error [42601]"), 400),
        (IsolationError("Restricted lab connection missing."), 503),
    ],
)
async def test_history_logs_error_and_next_request_works(history, error, status):
    history.search.side_effect = [error, ([{"title": "recovered"}], "synthetic SQL")]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        failed = await client.get("/api/red/chat/history", params={"q": "'", "session_id": str(history.session)})
        recovered = await client.get(
            "/api/red/chat/history", params={"q": "normal", "session_id": str(history.session)}
        )
    assert failed.status_code == status
    assert failed.json() == {"detail": str(error)}
    assert recovered.status_code == 200
    assert recovered.json() == {"results": [{"title": "recovered"}]}
    assert [call.kwargs["event_type"] for call in history.log.await_args_list] == ["DB_QUERY", "SQL_ERROR", "DB_QUERY"]
    assert history.log.await_args_list[1].kwargs["payload"] == {"error": str(error), "failed_query": "'"}


PDF_BYTES = b"%PDF-1.4 isolated test"


@pytest.fixture
def upload(monkeypatch, tmp_path):
    """Post to the real upload route with storage, Redis, ETL and SIEM replaced by doubles."""
    monkeypatch.chdir(tmp_path)
    env = SimpleNamespace(
        user=dict(id=str(uuid4()), lab_instance_id=str(uuid4()), role="red_team"),
        redis=AsyncMock(),
        process_pdf=AsyncMock(),
        log=AsyncMock(),
        store=SimpleNamespace(put=AsyncMock()),
        root=tmp_path,
    )
    monkeypatch.setattr(routes_red, "log_security_event", env.log)
    monkeypatch.setattr(routes_red, "ETLWorker", Mock(return_value=SimpleNamespace(process_stored_pdf=env.process_pdf)))

    async def current_user():
        return env.user

    async def database():
        yield object()

    async def redis_dependency():
        yield env.redis

    async def vector_store():
        return Mock()

    async def object_store():
        return env.store

    async def post(filename, content=PDF_BYTES):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/api/red/upload", files={"file": (filename, content, "application/pdf")})

    env.post = post
    red_app.dependency_overrides.update(
        {
            get_current_user_vulnerable: current_user,
            get_db: database,
            get_redis: redis_dependency,
            get_vector_store: vector_store,
            get_object_store: object_store,
        }
    )
    try:
        yield env
    finally:
        red_app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filename,expected,path,indexed",
    [
        ("normal.pdf", 200, "uploads/normal.pdf", False),
        ("../student_uploads/poison.pdf", 200, "student_uploads/poison.pdf", True),
        ("../../outside.pdf", 403, None, False),
        ("../student_uploads-other/outside.pdf", 403, None, False),
    ],
)
async def test_upload_keeps_traversal_exercise_inside_own_lab(upload, filename, expected, path, indexed):
    user, redis, process_pdf = upload.user, upload.redis, upload.process_pdf
    response = await upload.post(filename)
    assert response.status_code == expected
    # Nothing is written to the API host's disk.
    assert list(upload.root.iterdir()) == []
    key = f"{user['lab_instance_id']}/{path}"
    if path:
        upload.store.put.assert_awaited_once_with(key, PDF_BYTES)
        stored = next(c.kwargs for c in upload.log.await_args_list if c.kwargs["event_type"] == "FILE_UPLOAD")
        assert stored["payload"]["destination"] == key
    else:
        upload.store.put.assert_not_awaited()
        assert upload.log.call_args.kwargs["event_type"] == "FILE_UPLOAD_FAILED"
    if indexed:
        process_pdf.assert_awaited_once()
        assert process_pdf.call_args.args[0] is upload.store
        assert process_pdf.call_args.args[1] == key
        assert str(process_pdf.call_args.args[5]) == user["lab_instance_id"]
    else:
        process_pdf.assert_not_called()
    if expected == 200:
        job = redis.hset.call_args_list[0].kwargs["mapping"]
        assert job["user_id"] == user["id"]
        assert job["lab_instance_id"] == user["lab_instance_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,status",
    [(b"%PDF-" + b"x" * 64, 413), (b"MZ not a pdf", 415)],
)
async def test_upload_rejects_oversized_or_non_pdf_before_storing(upload, monkeypatch, content, status):
    monkeypatch.setattr(routes_red.settings, "UPLOAD_MAX_BYTES", 32)
    response = await upload.post("../student_uploads/poison.pdf", content)
    assert response.status_code == status
    upload.store.put.assert_not_awaited()
    assert list(upload.root.iterdir()) == []
    upload.redis.hset.assert_not_awaited()
    upload.process_pdf.assert_not_called()
    assert upload.log.call_args.kwargs["event_type"] == "FILE_UPLOAD_REJECTED"


@pytest.mark.asyncio
async def test_etl_socket_rejects_foreign_job(monkeypatch):
    user = dict(id=str(uuid4()), lab_instance_id=str(uuid4()))
    monkeypatch.setattr(routes_red, "get_current_user_vulnerable", AsyncMock(return_value=user))
    redis = AsyncMock()
    redis.hgetall.return_value = dict(user_id=str(uuid4()), lab_instance_id=user["lab_instance_id"])
    socket = SimpleNamespace(headers={}, close=AsyncMock(), accept=AsyncMock())
    await routes_red.etl_status_websocket(socket, uuid4(), redis, object())
    socket.close.assert_awaited_once_with(code=1008)
    socket.accept.assert_not_awaited()


def test_chat_route_takes_model_from_provider_dependency():
    route = next(route for route in chat_router.router.routes if route.path == "/chat/ask")
    calls = [dependency.call for dependency in route.dependant.dependencies]
    assert provider.get_llm_instance in calls
