"""Exercise flag seeding against in-memory SQLite (not PostgreSQL or Chroma)."""

import hashlib
import io
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pypdf import PdfReader
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.auth.service import verify_password
from app.db.models import ChatThread, CtfFlag, CtfSubmission, GameSession, LabSecret, User
from app.labs import exercises
from app.labs.service import get_exercise_secret
from tests.sqlite_async import AsyncDatabase

FLAG = re.compile(r"^flag\{[0-9a-f]{32}\}$")


class PdfIndex:
    """Records what the seed would index; returns the chunk count it is given."""

    def __init__(self, chunks=1):
        self.calls, self.chunks = [], chunks

    async def __call__(self, content, filename, lab_instance_id):
        self.calls.append(SimpleNamespace(content=content, filename=filename, lab=lab_instance_id))
        return self.chunks


@pytest.fixture
def labs():
    engine = create_engine("sqlite://")
    for model in (User, GameSession, ChatThread, CtfFlag, CtfSubmission, LabSecret):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        ids = [uuid4(), uuid4()]
        sessions = [GameSession(id=uuid4(), lab_instance_id=lab, team_id=uuid4()) for lab in ids]
        session.add_all(sessions)
        session.commit()
        yield SimpleNamespace(db=AsyncDatabase(session), session=session, ids=ids, sessions=sessions)
    engine.dispose()


def count(labs, model):
    return labs.session.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
async def test_seed_creates_three_hashed_flags_in_the_lab(labs):
    flags = await exercises.seed_exercise_flags(labs.db, labs.ids[0], PdfIndex())
    assert sorted(flags) == ["EX-03", "EX-04", "EX-05"]
    assert all(FLAG.match(value) for value in flags.values())
    assert len(set(flags.values())) == 3
    rows = list(labs.session.scalars(select(CtfFlag)))
    assert {row.lab_id for row in rows} == {str(labs.ids[0])}
    assert {row.flag_value for row in rows} == {hashlib.sha256(v.encode()).hexdigest() for v in flags.values()}
    assert all(row.reward > 0 for row in rows)


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_shows_values_once(labs):
    index = PdfIndex()
    assert await exercises.seed_exercise_flags(labs.db, labs.ids[0], index)
    labs.session.commit()
    assert await exercises.seed_exercise_flags(labs.db, labs.ids[0], index) == {}
    assert (count(labs, CtfFlag), count(labs, LabSecret), count(labs, User), count(labs, ChatThread)) == (3, 3, 1, 1)
    assert len(index.calls) == 1


@pytest.mark.asyncio
async def test_each_lab_gets_its_own_flags(labs):
    first = await exercises.seed_exercise_flags(labs.db, labs.ids[0], PdfIndex())
    second = await exercises.seed_exercise_flags(labs.db, labs.ids[1], PdfIndex())
    assert not set(first.values()) & set(second.values())
    for lab, flags in zip(labs.ids, (first, second)):
        hashes = set(labs.session.scalars(select(CtfFlag.flag_value).where(CtfFlag.lab_id == str(lab))))
        assert hashes == {hashlib.sha256(v.encode()).hexdigest() for v in flags.values()}
    assert len(set(labs.session.scalars(select(User.username)))) == 2  # One hidden staff user per lab.


@pytest.mark.asyncio
async def test_ex03_secret_is_readable_only_in_its_own_lab(labs):
    first = await exercises.seed_exercise_flags(labs.db, labs.ids[0], PdfIndex())
    assert await get_exercise_secret(labs.db, labs.ids[0], "EX-03") == first["EX-03"]
    assert await get_exercise_secret(labs.db, str(labs.ids[0]), "EX-03") == first["EX-03"]
    assert await get_exercise_secret(labs.db, labs.ids[1], "EX-03") is None  # Not seeded.
    assert await get_exercise_secret(labs.db, labs.ids[0], "EX-04") is None  # Kept only as a hash.


@pytest.mark.asyncio
async def test_ex04_flag_sits_in_a_hidden_staff_thread(labs):
    flags = await exercises.seed_exercise_flags(labs.db, labs.ids[0], PdfIndex())
    [staff] = labs.session.scalars(select(User))
    assert (staff.role, staff.lab_instance_id) == ("staff", labs.ids[0])
    assert staff.username.startswith("staff_")
    assert not any(verify_password(guess, staff.password_hash) for guess in ("", staff.username, "staff"))
    [thread] = labs.session.scalars(select(ChatThread))
    assert (thread.user_id, thread.session_id) == (staff.id, labs.sessions[0].id)
    assert any(flags["EX-04"] in message["content"] for message in thread.messages)


@pytest.mark.asyncio
async def test_ex05_flag_is_in_a_private_pdf_for_its_lab(labs):
    index = PdfIndex()
    flags = await exercises.seed_exercise_flags(labs.db, labs.ids[0], index)
    [call] = index.calls
    assert call.lab == labs.ids[0]
    assert call.filename.endswith(".pdf")
    text = "".join(page.extract_text() for page in PdfReader(io.BytesIO(call.content)).pages)
    assert flags["EX-05"] in text
    assert flags["EX-03"] not in text and flags["EX-04"] not in text


@pytest.mark.asyncio
async def test_private_pdf_indexer_marks_chunks_private():
    etl = SimpleNamespace(process_pdf=AsyncMock(return_value=2))
    redis, lab = object(), uuid4()
    index = exercises.private_pdf_indexer(etl, redis)
    assert await index(b"%PDF-", "handbook.pdf", lab) == 2
    kwargs = etl.process_pdf.await_args.kwargs
    assert (kwargs["access_level"], kwargs["lab_instance_id"], kwargs["redis"]) == ("private", lab, redis)
    assert (kwargs["file_content"], kwargs["filename"]) == (b"%PDF-", "handbook.pdf")


@pytest.mark.asyncio
async def test_failed_pdf_indexing_writes_nothing(labs):
    with pytest.raises(exercises.SeedError):
        await exercises.seed_exercise_flags(labs.db, labs.ids[0], PdfIndex(chunks=0))
    labs.session.rollback()
    assert (count(labs, CtfFlag), count(labs, LabSecret), count(labs, User), count(labs, ChatThread)) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_lab_without_game_session_is_rejected(labs):
    index = PdfIndex()
    with pytest.raises(exercises.SeedError):
        await exercises.seed_exercise_flags(labs.db, uuid4(), index)
    assert index.calls == []
    assert count(labs, CtfFlag) == 0
