"""Exercise flag seeding against in-memory SQLite (not PostgreSQL or Chroma)."""

import hashlib
import io
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

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


class Documents:
    """In-memory private document index keyed like Chroma's (lab, source) filter."""

    def __init__(self, fail=0):
        self.docs, self.calls, self.fail = {}, [], fail

    async def remove(self, lab_instance_id, source):
        self.calls.append("remove")
        self.docs.pop((lab_instance_id, source), None)

    async def index(self, content, source, lab_instance_id):
        self.calls.append("index")
        if self.fail:
            self.fail -= 1
            return 0
        self.docs[(lab_instance_id, source)] = content
        return 1


def pdf_text(content):
    return "".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)


def make_labs(*ids):
    engine = create_engine("sqlite://")
    for model in (User, GameSession, ChatThread, CtfFlag, CtfSubmission, LabSecret):
        model.__table__.create(engine)
    session = Session(engine, expire_on_commit=False)
    sessions = [GameSession(id=uuid4(), lab_instance_id=lab, team_id=uuid4()) for lab in ids]
    session.add_all(sessions)
    session.commit()
    return SimpleNamespace(db=AsyncDatabase(session), session=session, ids=list(ids), sessions=sessions, engine=engine)


@pytest.fixture
def labs():
    built = make_labs(uuid4(), uuid4())
    yield built
    built.session.close()
    built.engine.dispose()


def count(labs, model):
    return labs.session.scalar(select(func.count()).select_from(model))


async def seed(labs, lab=0):
    flags = await exercises.seed_exercise_flags(labs.db, labs.ids[lab])
    labs.session.commit()
    return flags


@pytest.mark.asyncio
async def test_seed_creates_three_hashed_flags_in_the_lab(labs):
    flags = await seed(labs)
    assert sorted(flags) == ["EX-03", "EX-04", "EX-05"]
    assert all(FLAG.match(value) for value in flags.values())
    assert len(set(flags.values())) == 3
    rows = list(labs.session.scalars(select(CtfFlag)))
    assert {row.lab_id for row in rows} == {str(labs.ids[0])}
    assert {row.flag_value for row in rows} == {hashlib.sha256(v.encode()).hexdigest() for v in flags.values()}
    assert all(row.reward > 0 for row in rows)


@pytest.mark.asyncio
async def test_seed_is_idempotent_and_shows_values_once(labs):
    assert await seed(labs)
    assert await seed(labs) == {}
    assert (count(labs, CtfFlag), count(labs, LabSecret), count(labs, User), count(labs, ChatThread)) == (3, 3, 1, 1)


@pytest.mark.asyncio
async def test_each_lab_gets_its_own_flags(labs):
    first, second = await seed(labs, 0), await seed(labs, 1)
    assert not set(first.values()) & set(second.values())
    for lab, flags in zip(labs.ids, (first, second)):
        hashes = set(labs.session.scalars(select(CtfFlag.flag_value).where(CtfFlag.lab_id == str(lab))))
        assert hashes == {hashlib.sha256(v.encode()).hexdigest() for v in flags.values()}


@pytest.mark.asyncio
async def test_labs_sharing_a_uuid_prefix_get_distinct_hidden_users():
    labs = make_labs(UUID("12345678-aaaa-4aaa-8aaa-aaaaaaaaaaa1"), UUID("12345678-bbbb-4bbb-8bbb-bbbbbbbbbbb2"))
    try:
        await seed(labs, 0)
        await seed(labs, 1)
        users = list(labs.session.scalars(select(User)))
        assert len({user.username for user in users}) == 2
        assert {user.lab_instance_id for user in users} == set(labs.ids)
    finally:
        labs.session.close()
        labs.engine.dispose()


@pytest.mark.asyncio
async def test_runtime_secrets_are_readable_only_in_their_own_lab(labs):
    flags = await seed(labs)
    assert await get_exercise_secret(labs.db, labs.ids[0], "EX-03") == flags["EX-03"]
    assert await get_exercise_secret(labs.db, str(labs.ids[0]), "EX-05") == flags["EX-05"]
    assert await get_exercise_secret(labs.db, labs.ids[1], "EX-03") is None  # Not seeded.
    assert await get_exercise_secret(labs.db, labs.ids[0], "EX-04") is None  # Kept only as a hash.


@pytest.mark.asyncio
async def test_ex04_flag_sits_in_a_hidden_staff_thread(labs):
    flags = await seed(labs)
    [staff] = labs.session.scalars(select(User))
    assert (staff.role, staff.lab_instance_id) == ("staff", labs.ids[0])
    assert staff.username == f"staff_{labs.ids[0].hex}"
    assert not any(verify_password(guess, staff.password_hash) for guess in ("", staff.username, "staff"))
    [thread] = labs.session.scalars(select(ChatThread))
    assert (thread.user_id, thread.session_id) == (staff.id, labs.sessions[0].id)
    assert any(flags["EX-04"] in message["content"] for message in thread.messages)


@pytest.mark.asyncio
async def test_private_document_is_built_from_the_stored_ex05_flag(labs):
    flags = await seed(labs)
    documents = Documents()
    await exercises.index_private_document(labs.db, labs.ids[0], documents)
    assert documents.calls == ["remove", "index"]  # Stable identity: old chunks go first.
    [((lab, source), content)] = documents.docs.items()
    assert (lab, source) == (labs.ids[0], exercises.PRIVATE_PDF_NAME)
    text = pdf_text(content)
    assert flags["EX-05"] in text
    assert flags["EX-03"] not in text and flags["EX-04"] not in text


@pytest.mark.asyncio
async def test_reindexing_replaces_the_document_instead_of_duplicating_it(labs):
    flags = await seed(labs)
    documents = Documents()
    for _ in range(2):
        await exercises.index_private_document(labs.db, labs.ids[0], documents)
    assert len(documents.docs) == 1
    assert flags["EX-05"] in pdf_text(next(iter(documents.docs.values())))


@pytest.mark.asyncio
async def test_failed_indexing_keeps_flags_and_a_rerun_recovers(labs):
    flags = await seed(labs)
    documents = Documents(fail=1)
    with pytest.raises(exercises.SeedError):
        await exercises.index_private_document(labs.db, labs.ids[0], documents)
    assert count(labs, CtfFlag) == 3 and documents.docs == {}
    assert await seed(labs) == {}  # The rerun creates no new flags ...
    await exercises.index_private_document(labs.db, labs.ids[0], documents)  # ... and indexes the stored one.
    assert flags["EX-05"] in pdf_text(next(iter(documents.docs.values())))


@pytest.mark.asyncio
async def test_database_failure_leaves_nothing_to_index(labs):
    await exercises.seed_exercise_flags(labs.db, labs.ids[0])
    labs.session.rollback()  # The seed transaction never commits.
    documents = Documents()
    with pytest.raises(exercises.SeedError):
        await exercises.index_private_document(labs.db, labs.ids[0], documents)
    assert documents.calls == [] and count(labs, CtfFlag) == 0


@pytest.mark.asyncio
async def test_lab_without_game_session_is_rejected(labs):
    with pytest.raises(exercises.SeedError):
        await exercises.seed_exercise_flags(labs.db, uuid4())
    assert count(labs, CtfFlag) == 0


@pytest.mark.asyncio
async def test_private_pdf_index_marks_chunks_private_and_removes_by_lab_and_source(monkeypatch):
    etl = SimpleNamespace(process_pdf=AsyncMock(return_value=2))
    store, redis, lab = SimpleNamespace(delete_documents=Mock()), object(), uuid4()
    documents = exercises.PrivatePdfIndex(etl, store, redis)
    assert await documents.index(b"%PDF-", "handbook.pdf", lab) == 2
    kwargs = etl.process_pdf.await_args.kwargs
    assert (kwargs["access_level"], kwargs["lab_instance_id"], kwargs["redis"]) == ("private", lab, redis)
    assert (kwargs["file_content"], kwargs["filename"]) == (b"%PDF-", "handbook.pdf")
    await documents.remove(lab, "handbook.pdf")
    store.delete_documents.assert_called_once_with(
        {"$and": [{"lab_instance_id": str(lab)}, {"source": "handbook.pdf"}]}
    )
