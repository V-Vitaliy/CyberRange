"""Per-lab exercise flags (EX-03, EX-04, EX-05); run once per lab by scripts/seed_exercises.py."""

import asyncio
import secrets
import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_or_create_hidden_user
from app.chat.service import add_seeded_thread
from app.ctf.service import register_flag
from app.db.ids import ensure_uuid
from app.labs.repository import LabSecretRepository
from app.labs.service import get_exercise_secret, get_session_for_lab

REWARDS = {"EX-03": 30, "EX-04": 20, "EX-05": 20}
PRIVATE_PDF_NAME = "staff_handbook_confidential.pdf"

# Exercises whose plaintext the lab needs after seeding: EX-03 for the assistant's
# context, EX-05 to rebuild its private document if indexing has to be repeated.
RUNTIME_SECRETS = {"EX-03", "EX-05"}


class PrivateDocuments(Protocol):
    async def remove(self, lab_instance_id: uuid.UUID, source: str) -> None: ...

    async def index(self, content: bytes, source: str, lab_instance_id: uuid.UUID) -> int: ...


class SeedError(Exception):
    """A secret-free message for the seeding CLI."""


def new_flag() -> str:
    return f"flag{{{secrets.token_hex(16)}}}"


def build_private_pdf(lines: list[str]) -> bytes:
    """A one-page text PDF, so the real ETL pipeline indexes it like any upload."""

    def escape(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text = "BT /F1 12 Tf 72 720 Td 16 TL " + " ".join(f"({escape(line)}) Tj T*" for line in lines) + " ET"
    stream = text.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(pdf)


class PrivatePdfIndex:
    """Index through the upload pipeline, but only for the admin role (access_level=private)."""

    def __init__(self, etl_worker, vector_store, redis):
        self.etl_worker, self.vector_store, self.redis = etl_worker, vector_store, redis

    async def remove(self, lab_instance_id: uuid.UUID, source: str) -> None:
        where = {"$and": [{"lab_instance_id": str(lab_instance_id)}, {"source": source}]}
        await asyncio.to_thread(self.vector_store.delete_documents, where)

    async def index(self, content: bytes, source: str, lab_instance_id: uuid.UUID) -> int:
        return await self.etl_worker.process_pdf(
            file_content=content,
            filename=source,
            task_id=uuid.uuid4(),
            redis=self.redis,
            lab_instance_id=lab_instance_id,
            access_level="private",
        )


async def seed_exercise_flags(db: AsyncSession, lab_instance_id) -> dict[str, str]:
    """
    Create the lab's missing exercise flags inside the caller's transaction.

    Returns the plaintext of the flags created now, so the instructor sees each value once;
    exercises seeded earlier are skipped and not returned. The EX-05 document is indexed
    separately, after commit, by index_private_document.
    """
    lab = ensure_uuid(lab_instance_id)
    session = await get_session_for_lab(db, lab)
    if session is None:
        raise SeedError("The lab has no game session; run admin_setup first.")
    seeded = await LabSecretRepository.exercises_for_lab(db, lab)
    flags = {exercise: new_flag() for exercise in REWARDS if exercise not in seeded}

    for exercise, value in flags.items():
        flag = await register_flag(db, lab, value, REWARDS[exercise])
        LabSecretRepository.add(
            db,
            lab_instance_id=lab,
            exercise=exercise,
            flag_id=flag.id,
            value=value if exercise in RUNTIME_SECRETS else None,
        )
        if exercise == "EX-04":
            staff = await get_or_create_hidden_user(db, f"staff_{lab.hex}", "staff", lab)
            await add_seeded_thread(
                db,
                staff.id,
                session.id,
                "Exam room access",
                [
                    {"role": "user", "content": f"Note to self: the exam room access code is {value}"},
                    {"role": "ai", "content": "I have noted the access code."},
                ],
            )
    await db.flush()
    return flags


async def index_private_document(db: AsyncSession, lab_instance_id, documents: PrivateDocuments) -> int:
    """
    (Re)index the EX-05 document from the committed flag. The document has a stable
    identity (lab, file name): old chunks are removed first, so repeating is safe and
    the index never holds a flag the database does not know.
    """
    lab = ensure_uuid(lab_instance_id)
    value = await get_exercise_secret(db, lab, "EX-05")
    if value is None:
        raise SeedError("The lab has no committed EX-05 flag; run the seed first.")
    await documents.remove(lab, PRIVATE_PDF_NAME)
    lines = ["Staff handbook (confidential).", "Internal access code:", value]
    chunks = await documents.index(build_private_pdf(lines), PRIVATE_PDF_NAME, lab)
    if not chunks:
        raise SeedError("The private document was not indexed; the flags are saved, repeat the command.")
    return chunks
