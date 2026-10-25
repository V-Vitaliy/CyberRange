# Backend tests

- `unit/`: settings and request queue.
- `api/`: HTTP/WebSocket routes, file uploads, and application lifecycle.
- `services/`: data access, switchable defenses, RAG, SOC, and email tools.
- `test_admin_setup.py`: database setup using doubles; can also run separately with standard-library `unittest` and no backend dependencies.

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
PYTHONPATH=backend python -m pytest -q -ra backend/tests/services
```

CI runs `backend/tests` recursively and includes all groups.
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
lifespan, Redis, Chroma, LLM or a network HTTP server. See
[ISSUE_59_ISOLATION.md](../../docs/tasks/ISSUE_59_ISOLATION.md) for results and reproduction.
