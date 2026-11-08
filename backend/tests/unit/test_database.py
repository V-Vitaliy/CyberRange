import pytest

from app.db import database


@pytest.mark.asyncio
async def test_create_schema_creates_tables_then_applies_upgrades():
    calls = []

    class Connection:
        async def run_sync(self, fn):
            calls.append(fn)

        async def execute(self, statement):
            calls.append(str(statement))

    await database.create_schema(Connection())
    assert calls[0] == database.Base.metadata.create_all
    assert calls[1:] == list(database.SCHEMA_UPGRADES)
    # create_all does not add columns to tables that already exist.
    assert any("ADD COLUMN IF NOT EXISTS login_hardening_enabled" in s for s in database.SCHEMA_UPGRADES)
