import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.documents import object_store
from app.documents.object_store import ObjectStore, PathTraversalError, resolve_upload_target

LAB = str(uuid4())


@pytest.mark.parametrize(
    "filename,path,indexed",
    [
        ("normal.pdf", "uploads/normal.pdf", False),
        ("notes/../plan.pdf", "uploads/plan.pdf", False),
        ("../student_uploads/poison.pdf", "student_uploads/poison.pdf", True),
        ("../student_uploads/deep/../doc.pdf", "student_uploads/doc.pdf", True),
        # POSIX semantics, as with the former local paths: a backslash is part of the name.
        ("..\\student_uploads\\x.pdf", "uploads/..\\student_uploads\\x.pdf", False),
    ],
)
def test_upload_target_keeps_traversal_exercise_inside_lab_prefix(filename, path, indexed):
    target = resolve_upload_target(LAB, filename)
    assert target.key == f"{LAB}/{path}"
    assert target.indexed is indexed


@pytest.mark.parametrize(
    "filename",
    [
        "../../outside.pdf",
        "../student_uploads-other/x.pdf",
        "/etc/passwd",
        f"../../{uuid4()}/student_uploads/x.pdf",
        "../student_uploads/",
        "../student_uploads/..",
        "../",
        ".",
        "",
    ],
)
def test_upload_target_rejects_paths_outside_lab_folders(filename):
    with pytest.raises(PathTraversalError):
        resolve_upload_target(LAB, filename)


@pytest.mark.asyncio
async def test_put_and_get_call_s3_off_the_event_loop():
    threads = []

    def record(result):
        def call(**kwargs):
            threads.append(threading.get_ident())
            return result

        return call

    body = SimpleNamespace(read=lambda: b"%PDF-stored")
    client = SimpleNamespace(
        put_object=Mock(side_effect=record(None)), get_object=Mock(side_effect=record({"Body": body}))
    )
    store = ObjectStore(client, "lab-uploads")
    key = f"{LAB}/student_uploads/doc.pdf"

    await store.put(key, b"%PDF-data")
    assert await store.get(key) == b"%PDF-stored"
    client.put_object.assert_called_once_with(
        Bucket="lab-uploads", Key=key, Body=b"%PDF-data", ContentType="application/pdf"
    )
    client.get_object.assert_called_once_with(Bucket="lab-uploads", Key=key)
    assert len(threads) == 2 and threading.get_ident() not in threads


@pytest.fixture
def boto3(monkeypatch):
    # boto3 is not installed in the light test environment; the real client is checked in CI compose.
    client = Mock()
    module = SimpleNamespace(client=Mock(return_value=client))
    monkeypatch.setitem(sys.modules, "boto3", module)
    monkeypatch.setattr(object_store.settings, "S3_ENDPOINT_URL", "http://storage.invalid:8333")
    monkeypatch.setattr(object_store.settings, "S3_BUCKET", "lab-uploads")
    monkeypatch.setattr(object_store.settings, "S3_ACCESS_KEY", "a" * 20)
    monkeypatch.setattr(object_store.settings, "S3_SECRET_KEY", "b" * 40)
    return SimpleNamespace(module=module, client=client)


def test_from_settings_connects_with_configured_identity_and_checks_bucket(boto3):
    store = ObjectStore.from_settings()
    kwargs = boto3.module.client.call_args.kwargs
    assert boto3.module.client.call_args.args == ("s3",)
    assert kwargs["endpoint_url"] == "http://storage.invalid:8333"
    assert (kwargs["aws_access_key_id"], kwargs["aws_secret_access_key"]) == ("a" * 20, "b" * 40)
    boto3.client.head_bucket.assert_called_once_with(Bucket="lab-uploads")
    assert store.bucket == "lab-uploads"


@pytest.mark.parametrize("missing", ["S3_ACCESS_KEY", "S3_SECRET_KEY"])
def test_from_settings_refuses_to_start_without_credentials(boto3, monkeypatch, missing):
    monkeypatch.setattr(object_store.settings, missing, "")
    with pytest.raises(RuntimeError, match=missing):
        ObjectStore.from_settings()
    boto3.module.client.assert_not_called()


def test_from_settings_reports_missing_bucket(boto3):
    boto3.client.head_bucket.side_effect = RuntimeError("404 Not Found")
    with pytest.raises(RuntimeError, match="lab-uploads"):
        ObjectStore.from_settings()
