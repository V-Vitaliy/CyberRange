"""CTF HTTP/auth/repository regressions with real SQLite queries, not PostgreSQL integration.

Only the database transport and SIEM writer are replaced. Flag lookup, identity
validation and submission writes execute the production repository methods.
"""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import routes_ctf
from app.core.security import create_access_token
from app.db.database import get_db
from app.db.models import CtfFlag, CtfSubmission, GameSession, User
from app.db.repository import CtfRepository
from app.main import app, red_app


class AsyncDatabase:
    """Run the application's awaited repository SQL against an in-memory database."""

    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    def add(self, row):
        self.session.add(row)

    async def commit(self):
        self.session.commit()


def flag_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@pytest.fixture
def ctf(monkeypatch):
    engine = create_engine('sqlite://')
    for model in (User, GameSession, CtfFlag, CtfSubmission):
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        labs, teams = [uuid4(), uuid4()], [uuid4(), uuid4()]
        users, tokens = [], []
        for i, lab in enumerate(labs):
            user = User(id=uuid4(), username=f'ctf-student-{i}', password_hash='unused',
                        role='red_team', lab_instance_id=lab)
            session.add(user)
            session.add(GameSession(id=uuid4(), lab_instance_id=lab, team_id=teams[i]))
            users.append(user)
            tokens.append(create_access_token(dict(
                id=str(user.id), sub=user.username, role=user.role, lab_instance_id=str(lab))))
        # Insert the foreign row first: an unscoped .first() must fail the tests.
        flags = [
            CtfFlag(id=1, lab_id=str(labs[1]), flag_value=flag_hash('shared-test-flag'), reward=91),
            CtfFlag(id=2, lab_id=str(labs[0]), flag_value=flag_hash('shared-test-flag'), reward=17),
            CtfFlag(id=3, lab_id=str(labs[1]), flag_value=flag_hash('foreign-only-test-flag'), reward=42),
            CtfFlag(id=4, lab_id='legacy-scenario', flag_value=flag_hash('legacy-test-flag'), reward=10),
        ]
        session.add_all(flags)
        session.commit()
        db = AsyncDatabase(session)

        async def database():
            yield db

        prior = dict(red_app.dependency_overrides)
        red_app.dependency_overrides[get_db] = database
        log = AsyncMock()
        monkeypatch.setattr(routes_ctf, 'log_security_event', log)
        try:
            yield SimpleNamespace(db=db, session=session, labs=labs, teams=teams,
                                  users=users, tokens=tokens, flags=flags, log=log)
        finally:
            red_app.dependency_overrides.clear()
            red_app.dependency_overrides.update(prior)
    engine.dispose()


async def submit(ctf, value, lab=0, **extra):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        return await client.post('/api/red/ctf/submit',
                                 headers={'Authorization': f'Bearer {ctf.tokens[lab]}'},
                                 json=dict(flag_value=value, **extra))


@pytest.mark.asyncio
@pytest.mark.parametrize('lab,flag_id,reward', [(0, 2, 17), (1, 1, 91)])
async def test_same_value_is_accepted_only_for_authenticated_lab(ctf, lab, flag_id, reward):
    response = await submit(ctf, 'shared-test-flag', lab)
    assert response.status_code == 200
    assert response.json() == {
        'success': True, 'message': f'Flag captured successfully! You earned {reward} points.',
        'reward': reward,
    }
    row = ctf.session.scalars(select(CtfSubmission)).one()
    assert (row.flag_id, row.team_id, row.lab_instance_id) == (
        flag_id, ctf.teams[lab], ctf.labs[lab])
    ctf.log.assert_awaited_once()
    event = ctf.log.call_args.kwargs
    assert event['event_type'] == 'CTF_FLAG_CAPTURED'
    assert event['lab_instance_id'] == str(ctf.labs[lab])
    assert event['payload'] == dict(flag_id=flag_id, reward=reward, submitted_by=str(ctf.users[lab].id))


@pytest.mark.asyncio
async def test_foreign_unknown_and_legacy_flags_have_same_rejection(ctf):
    responses = []
    for value in ('foreign-only-test-flag', 'unknown-test-flag', 'legacy-test-flag'):
        # Client-provided lab/team identifiers cannot change the authenticated boundary.
        response = await submit(ctf, value, lab_instance_id=str(ctf.labs[1]), team_id=str(ctf.teams[1]))
        responses.append((response.status_code, response.json()))
    assert responses == [(400, {'detail': 'Invalid flag.'})] * 3
    assert list(ctf.session.scalars(select(CtfSubmission))) == []
    assert ctf.log.await_count == 3
    assert all(call.kwargs['event_type'] == 'CTF_SUBMIT_FAILED'
               and call.kwargs['lab_instance_id'] == str(ctf.labs[0])
               for call in ctf.log.await_args_list)
    assert all('reward' not in call.kwargs['payload'] for call in ctf.log.await_args_list)


@pytest.mark.asyncio
async def test_resubmission_creates_no_second_result_or_success_event(ctf):
    first = await submit(ctf, 'shared-test-flag')
    again = await submit(ctf, 'shared-test-flag')
    other_team = await submit(ctf, 'shared-test-flag', lab=1)
    assert first.status_code == other_team.status_code == 200
    assert again.status_code == 400
    assert again.json() == {'detail': 'Flag already captured by your team.'}
    rows = list(ctf.session.scalars(select(CtfSubmission)))
    assert len(rows) == 2
    assert {(row.team_id, row.flag_id) for row in rows} == {(ctf.teams[0], 2), (ctf.teams[1], 1)}
    assert ctf.log.await_count == 2
    assert all(call.kwargs['event_type'] == 'CTF_FLAG_CAPTURED' for call in ctf.log.await_args_list)


@pytest.mark.asyncio
async def test_repository_normalizes_uuid_and_requires_both_hash_and_lab(ctf):
    for lab, flag_id in [(0, 2), (1, 1)]:
        found = await CtfRepository.get_flag_by_value(
            ctf.db, flag_hash('shared-test-flag'), str(ctf.labs[lab]).upper())
        assert found.id == flag_id
    assert await CtfRepository.get_flag_by_value(
        ctf.db, flag_hash('foreign-only-test-flag'), ctf.labs[0]) is None
    assert await CtfRepository.get_flag_by_value(
        ctf.db, flag_hash('unknown-test-flag'), ctf.labs[1]) is None
