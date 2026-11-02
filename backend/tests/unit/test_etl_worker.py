from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

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
