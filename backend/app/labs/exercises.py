"""Per-lab exercise flags (EX-03, EX-04, EX-05); run once per lab by scripts/seed_exercises.py."""

import secrets
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import get_or_create_hidden_user
from app.chat.service import add_seeded_thread
from app.ctf.service import register_flag
from app.db.ids import ensure_uuid
from app.labs.repository import LabSecretRepository
from app.labs.service import get_session_for_lab

REWARDS = {"EX-03": 30, "EX-04": 20, "EX-05": 20}
PRIVATE_PDF_NAME = "staff_handbook_confidential.pdf"

# (content, filename, lab_instance_id) -> number of indexed chunks
PdfIndexer = Callable[[bytes, str, uuid.UUID], Awaitable[int]]


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


def private_pdf_indexer(etl_worker, redis) -> PdfIndexer:
    """Index through the upload pipeline, but only for the admin role (access_level=private)."""

    async def index(content: bytes, filename: str, lab_instance_id: uuid.UUID) -> int:
        return await etl_worker.process_pdf(
            file_content=content,
            filename=filename,
            task_id=uuid.uuid4(),
            redis=redis,
            lab_instance_id=lab_instance_id,
            access_level="private",
        )

    return index


async def seed_exercise_flags(db: AsyncSession, lab_instance_id, index_private_pdf: PdfIndexer) -> dict[str, str]:
    """
    Create the lab's missing exercise flags inside the caller's transaction.

    Returns the plaintext of the flags created now, so the instructor sees each value once;
    exercises seeded earlier are skipped and not returned.
    """
    lab = ensure_uuid(lab_instance_id)
    session = await get_session_for_lab(db, lab)
    if session is None:
        raise SeedError("The lab has no game session; run admin_setup first.")
    missing = [exercise for exercise in REWARDS if exercise not in await LabSecretRepository.exercises_for_lab(db, lab)]
    flags = {exercise: new_flag() for exercise in missing}

    # Index first: if Chroma or the model fails, no flag without its document is written.
    if "EX-05" in flags:
        lines = ["Staff handbook (confidential).", "Internal access code:", flags["EX-05"]]
        if not await index_private_pdf(build_private_pdf(lines), PRIVATE_PDF_NAME, lab):
            raise SeedError("The private document was not indexed; nothing was written.")

    for exercise, value in flags.items():
        flag = await register_flag(db, lab, value, REWARDS[exercise])
        # Only EX-03 needs the plaintext at runtime (the assistant's context).
        LabSecretRepository.add(
            db, lab_instance_id=lab, exercise=exercise, flag_id=flag.id, value=value if exercise == "EX-03" else None
        )
        if exercise == "EX-04":
            staff = await get_or_create_hidden_user(db, f"staff_{lab.hex[:8]}", "staff", lab)
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
