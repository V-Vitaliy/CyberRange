from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from app.api import routes_red
from app.core.redis_client import get_redis
from app.core.security import get_current_user_vulnerable
from app.db.chroma_client import get_vector_store
from app.db.database import get_db
from app.main import app, red_app
from app.services import etl_worker


@pytest.mark.asyncio
@pytest.mark.parametrize("filename,expected,indexed", [
    ("normal.pdf", 200, False),
    ("../student_uploads/poison.pdf", 200, True),
    ("../../outside.pdf", 403, False),
    ("../student_uploads-other/outside.pdf", 403, False),
])
async def test_upload_keeps_traversal_exercise_inside_own_lab(monkeypatch, tmp_path, filename, expected, indexed):
    monkeypatch.chdir(tmp_path)
    user = dict(id=str(uuid4()), lab_instance_id=str(uuid4()), role="red_team")
    redis = AsyncMock()
    process_pdf = AsyncMock()
    monkeypatch.setattr(routes_red, "log_security_event", AsyncMock())
    monkeypatch.setattr(routes_red, "ETLWorker", Mock(return_value=SimpleNamespace(process_pdf=process_pdf)))

    class File:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def write(self, data):
            self.path.write_bytes(data)

    def open_file(path, mode):
        result = File()
        result.path = Path(path)
        return result

    monkeypatch.setattr(routes_red.aiofiles, "open", open_file)

    async def current_user():
        return user

    async def database():
        yield object()

    async def redis_dependency():
        yield redis

    async def vector_store():
        return Mock()

    red_app.dependency_overrides.update({get_current_user_vulnerable: current_user, get_db: database,
                                         get_redis: redis_dependency, get_vector_store: vector_store})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/red/upload", files={"file": (filename, b"isolated test", "application/pdf")})
    finally:
        red_app.dependency_overrides.clear()
    assert response.status_code == expected
    assert not (tmp_path / "lab_uploads" / "outside.pdf").exists()
    if indexed:
        process_pdf.assert_awaited_once()
        assert str(process_pdf.call_args.args[4]) == user["lab_instance_id"]
    else:
        process_pdf.assert_not_called()
    if expected == 200:
        job = redis.hset.call_args_list[0].kwargs["mapping"]
        assert job["user_id"] == user["id"]
        assert job["lab_instance_id"] == user["lab_instance_id"]


@pytest.mark.asyncio
async def test_etl_marks_every_chunk_with_lab_id(monkeypatch):
    worker = etl_worker.ETLWorker.__new__(etl_worker.ETLWorker)
    worker.batch_size = 100
    worker.vector_store = Mock()
    worker._chunk_text_with_overlap = Mock(return_value=["one", "two"])
    worker._estimate_tokens = Mock(return_value=1)
    monkeypatch.setattr(etl_worker, "PdfReader", Mock(return_value=SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "document")])) )
    lab_id = uuid4()
    result = await worker.process_pdf(b"stub", "test.pdf", uuid4(), AsyncMock(), lab_id)
    assert result == 2
    metadata = worker.vector_store.add_documents.call_args.kwargs["metadatas"]
    assert all(item["lab_instance_id"] == str(lab_id) for item in metadata)
    assert all(item["access_level"] == "public" for item in metadata)


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
