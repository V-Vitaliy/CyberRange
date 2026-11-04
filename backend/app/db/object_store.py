import asyncio
import posixpath
from dataclasses import dataclass

from fastapi import Request

from app.core.config import settings

UPLOAD_FOLDER = "uploads"
INDEXED_FOLDER = "student_uploads"


class PathTraversalError(ValueError):
    """The upload path leaves the lab's upload folders."""


@dataclass(frozen=True)
class UploadTarget:
    key: str
    indexed: bool


def resolve_upload_target(lab_instance_id: str, filename: str) -> UploadTarget:
    """Map an untrusted filename to an object key under the lab's prefix.

    The filename is joined to uploads/ without sanitizing, so ../student_uploads/ reaches
    the indexed folder (the intended exercise); anything outside both folders is rejected.
    """
    path = posixpath.normpath(posixpath.join(UPLOAD_FOLDER, filename or ""))
    folder, _, name = path.partition("/")
    if folder not in (UPLOAD_FOLDER, INDEXED_FOLDER) or not name:
        raise PathTraversalError("Wrong directory.")
    return UploadTarget(key=f"{lab_instance_id}/{path}", indexed=folder == INDEXED_FOLDER)


class ObjectStore:
    """Lab uploads in an S3-compatible bucket; blocking client calls run in worker threads."""

    def __init__(self, client, bucket: str):
        self.client = client
        self.bucket = bucket

    @classmethod
    def from_settings(cls) -> "ObjectStore":
        for name in ("S3_ACCESS_KEY", "S3_SECRET_KEY"):
            if not getattr(settings, name):
                raise RuntimeError(f"{name} is required: lab uploads are stored in object storage")
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name="us-east-1",
        )
        try:
            client.head_bucket(Bucket=settings.S3_BUCKET)
        except Exception as exc:
            raise RuntimeError(f"Object storage bucket {settings.S3_BUCKET!r} is not reachable") from exc
        return cls(client, settings.S3_BUCKET)

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType="application/pdf"
        )

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._read, key)

    def _read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


def get_object_store(request: Request) -> ObjectStore:
    return request.app.state.object_store
