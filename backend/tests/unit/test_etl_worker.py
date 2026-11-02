from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.services import etl_worker


@pytest.mark.asyncio
async def test_etl_marks_every_chunk_with_lab_id(monkeypatch):
    worker = etl_worker.ETLWorker.__new__(etl_worker.ETLWorker)
    worker.batch_size = 100
    worker.vector_store = Mock()
    worker._chunk_text_with_overlap = Mock(return_value=["one", "two"])
    worker._estimate_tokens = Mock(return_value=1)
    monkeypatch.setattr(
        etl_worker,
        "PdfReader",
        Mock(return_value=SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "document")])),
    )
    lab_id = uuid4()
    result = await worker.process_pdf(b"stub", "test.pdf", uuid4(), AsyncMock(), lab_id)
    assert result == 2
    metadata = worker.vector_store.add_documents.call_args.kwargs["metadatas"]
    assert all(item["lab_instance_id"] == str(lab_id) for item in metadata)
    assert all(item["access_level"] == "public" for item in metadata)


@pytest.mark.asyncio
async def test_etl_rejects_pdf_over_page_limit(monkeypatch):
    worker = etl_worker.ETLWorker.__new__(etl_worker.ETLWorker)
    worker.vector_store = Mock()
    pages = [SimpleNamespace(extract_text=lambda: "page")] * (settings.PDF_MAX_PAGES + 1)
    monkeypatch.setattr(etl_worker, "PdfReader", Mock(return_value=SimpleNamespace(pages=pages)))
    redis = AsyncMock()
    assert await worker.process_pdf(b"stub", "large.pdf", uuid4(), redis, uuid4()) == 0
    assert redis.hset.call_args.kwargs["mapping"]["status"] == "Failed"
    worker.vector_store.add_documents.assert_not_called()


@pytest.mark.asyncio
async def test_etl_failure_reports_generic_status_to_client(monkeypatch, caplog):
    internal = "Could not connect to tenant default_tenant at chromadb:8000"
    worker = etl_worker.ETLWorker.__new__(etl_worker.ETLWorker)
    monkeypatch.setattr(etl_worker, "PdfReader", Mock(side_effect=RuntimeError(internal)))
    redis = AsyncMock()
    assert await worker.process_pdf(b"stub", "test.pdf", uuid4(), redis, uuid4()) == 0
    status = redis.hset.call_args.kwargs["mapping"]
    assert status["status"] == "Failed"
    assert status["message"] == etl_worker.ETL_ERROR_MESSAGE
    assert any(record.exc_info and internal in str(record.exc_info[1]) for record in caplog.records)
