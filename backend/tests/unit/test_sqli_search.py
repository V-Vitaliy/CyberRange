"""Fail-closed configuration checks; PostgreSQL checks are in integration/."""
import json
from uuid import uuid4
from unittest.mock import Mock

import pytest

from app.db import sqli_search
from app.db.sqli_isolation import IsolationError, LabObjects


@pytest.mark.parametrize('problem', ['missing', 'malformed', 'foreign_lab', 'admin', 'other_db', 'other_host'])
@pytest.mark.asyncio
async def test_bad_reader_configuration_never_opens_engine(tmp_path, monkeypatch, problem):
    lab = LabObjects(uuid4())
    control = 'postgresql+asyncpg://admin:private-admin-password@localhost:5432/control'
    reader = f'postgresql+asyncpg://{lab.reader}:private-reader-password@localhost:5432/control'
    mapping = {str(lab.lab_id): reader}
    if problem == 'foreign_lab':
        mapping = {str(uuid4()): reader}
    elif problem == 'admin':
        mapping[str(lab.lab_id)] = control
    elif problem == 'other_db':
        mapping[str(lab.lab_id)] = reader.replace('/control', '/other')
    elif problem == 'other_host':
        mapping[str(lab.lab_id)] = reader.replace('@localhost', '@other-host')
    path = tmp_path / 'readers.json'
    path.write_text('not json' if problem == 'malformed' else json.dumps(mapping))
    monkeypatch.setattr(sqli_search.settings, 'DATABASE_URL', control)
    monkeypatch.setattr(sqli_search.settings, 'SQLI_CONNECTIONS_FILE', '' if problem == 'missing' else str(path))
    create = Mock()
    monkeypatch.setattr(sqli_search, 'create_async_engine', create)
    with pytest.raises(IsolationError) as error:
        await sqli_search.search_history(lab.lab_id, uuid4(), 'normal')
    create.assert_not_called()
    assert 'password' not in str(error.value)
    assert control not in str(error.value)
    assert reader not in str(error.value)
