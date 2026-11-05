# Backend tests

Tests are grouped by the module they verify. Add new tests to the existing file
of that module instead of creating a file per task.

- `unit/test_<module>.py`: one file per application module (`config`, `auth`, `labs`,
  `llm_engine`, `rag_service`, `soc`, `defense`, ...). `unit/test_admin_setup.py`
  covers `scripts/admin_setup.py` with doubles and can also run with standard-library
  `unittest` and no backend dependencies.
- `api/test_<router>.py`: HTTP/WebSocket routes (`routes_red`, `routes_blue`,
  `routes_ctf`) and application wiring (`main`).
- `integration/`: opt-in tests against real PostgreSQL.
- Shared fixtures: `conftest.py` (ASGI client) and `unit/conftest.py` (email tool
  harness). `openai_doubles.py` holds offline HTTP doubles for the OpenAI-compatible
  adapter.

The default tests use external service doubles: PostgreSQL, Redis, Chroma, LLM,
and SMTP do not need to run. They do not establish compatibility with real
services. SQLi API tests check awaited search, authorization, error events and
recovery using doubles; configuration tests verify no connection is opened for
missing, privileged, mismatched or malformed reader configuration.

From the repository root, in the activated virtual environment:

```bash
python -m pip install -r backend/requirements-test.txt
PYTHONPATH=backend python -m pytest -q -ra backend/tests
```

Run an individual group the same way:

```bash
PYTHONPATH=backend python -m pytest -q -ra backend/tests/unit
```

CI runs `backend/tests` recursively and includes all groups. A separate CI job runs
both PostgreSQL integration checks against a PostgreSQL service container and fails
if either check skips itself.

`api/test_routes_ctf.py` exercises the mounted submit route with real signed lab scopes,
identity checks, repository SQL and submission writes using in-memory SQLite.
It covers two labs sharing a flag value, foreign/unknown/legacy flag rejection,
duplicate capture, recorded team/lab/reward and UUID normalization. Only the DB
transport and SIEM writer are replaced; SIEM assertions check calls, not persisted
events. These regressions do not verify PostgreSQL or real SIEM persistence.
Frontend tests remain in `frontend/tests`; run them from the repository root:

```bash
node --test frontend/tests/*.test.mjs
```

## Separate PostgreSQL setup check

Run `integration/test_admin_setup_postgres.py` separately from the mocked suite.
It requires prepared backend dependencies and an explicitly designated test
PostgreSQL database. The test creates tables and uniquely named records, retains
records for inspection, and does not perform reset/teardown. Without both
`CYBERRANGE_SETUP_TEST_DATABASE_URL` and `CYBERRANGE_SETUP_TEST_DATABASE_NAME`,
it is skipped. `.env` alone does not authorize this run.

With both variables explicitly set, run:

```bash
.venv/bin/python backend/tests/integration/test_admin_setup_postgres.py -v
```

The [integration test](integration/test_admin_setup_postgres.py) checks real
setup, login, preservation and transaction rollback without application lifespan.

## Separate PostgreSQL SQLi check

`integration/test_sqli_isolation_postgres.py` requires both
`CYBERRANGE_SQLI_TEST_DATABASE_URL` and `CYBERRANGE_SQLI_TEST_DATABASE_NAME`,
with an explicitly designated test database. Run it separately:

```bash
.venv/bin/python backend/tests/integration/test_sqli_isolation_postgres.py -v
```

It retains two new synthetic labs and their roles/views, and tests actual ACLs,
intentional injection, foreign-lab rejection, error recovery and persisted SIEM
events through the mounted HTTP route and real auth. It does not start application
lifespan, Redis, Chroma, LLM or a network HTTP server.
