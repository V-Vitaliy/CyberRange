# Backend tests

- `unit/`: settings and request queue.
- `api/`: HTTP/WebSocket routes, file uploads, and application lifecycle.
- `services/`: data access, switchable defenses, RAG, SOC, and email tools.
- `test_admin_setup.py`: database setup using doubles; can also run separately with standard-library `unittest` and no backend dependencies.

The default tests use external service doubles: PostgreSQL, Redis, Chroma, LLM,
and SMTP do not need to run. They do not establish compatibility with real
services. The SQLi check is marked `xfail` while the repository search method
is missing.

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

Commands, limitations, and real integration results are documented in
[ISSUE_58_VALIDATION.md](../../ISSUE_58_VALIDATION.md).
