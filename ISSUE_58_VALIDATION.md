# Issue #58: real PostgreSQL setup validation

Validated on 2026-10-24 from baseline `ac4b5ce`, branch `Cyberrange_0.1`.
No setup implementation defect was reproduced; runtime code was not changed.

## Environment

- User-designated test database: `cyberrange_setup_test`, localhost:5432.
- PostgreSQL 15.19; database initially had no public tables.
- Repository `.venv`: Python 3.14.4, SQLAlchemy 2.1.6, asyncpg 0.32.0,
  bcrypt 5.0.0, FastAPI 0.162.0, httpx 0.28.1, PyJWT 2.15.1.
- Containers were already running; validation did not start or stop containers.
- No reset, teardown, migration, document/flag seeding, or package installation.

## Real integration results

`backend/tests/integration/test_admin_setup_postgres.py` passed twice. The first
run exercised the empty database (9 CLI invocations); the second also checked a
real SQL INSERT failure through the CLI (10 CLI invocations).

| Check | Evidence |
| --- | --- |
| Missing tables | Real `create_all` created all six application tables. |
| Initial setup | CLI created red/blue users and one session with the requested lab UUID; default team is the red user's UUID. |
| Passwords | Actual bcrypt hash/verify, including whitespace, Unicode and a password longer than 72 UTF-8 bytes. |
| Login and session | Mounted `/api/red/login`, `/api/blue/login`, and authenticated `/api/blue/me` returned 200; profile session ID matched setup. Signed JWT claims and red lab scope matched the lab. |
| Login rejection | Incorrect passwords returned 401; attempting the other team's login returned 403. |
| Login audit | Red login persisted its actual `RED_TEAM_LOGIN` audit row. |
| Repeat | CLI without password environment variables preserved all IDs, hashes, changed budget, prompt, defenses, solo setting, chat messages, and investigated audit event. Full table snapshots matched before/after. |
| Conflicts | Role, lab, explicit team, duplicate red/blue names and multiple sessions caused nonzero CLI exit and no changes to table snapshots. |
| Partial lab | Existing blue user/session retained their IDs, team and budget; only missing red user was added. |
| Transaction failure | After flushing real user/session inserts, PostgreSQL division-by-zero rolled back all changes. On the initially empty database, DDL also rolled back. |
| CLI SQL failure | An out-of-range integer budget caused an actual INSERT failure: nonzero exit, no success JSON, no partial user/session records. |
| Connection failure | A nonexistent test database caused nonzero CLI exit and sanitized stderr. |
| Secret handling | All captured CLI outputs checked for the supplied DSN, DB password and generated user passwords. None appeared. |

The HTTP checks used `httpx.ASGITransport` with the actual application and routes,
real authentication dependencies, repositories, SQLAlchemy, bcrypt and PostgreSQL.
No database/auth mocks or dependency overrides were used. Application lifespan
was intentionally not run: this does **not** establish Redis, Chroma, LLM,
Auto-SOC, external HTTP serving, frontend or complete startup compatibility.

Test data remains for inspection. Each run used a unique `setup58_<uuid>` username
prefix; the test never deletes records. Retained run prefixes:

- `setup58_11d9ba95ca4e4ac8a6184d3440f9ce24`
- `setup58_1451918d970b424490e1413f4a7aeab8`

The partial-lab scenario deliberately leaves two sessions in its own fixture lab
to validate ambiguity rejection. It does not alter the primary fixture lab.

## Commands and reproduction

From the repository root, enter the test URL locally using hidden input; do not
put credentials into this report. Both variables are mandatory: `.env` alone
does not enable integration tests. The database name must match the URL and the
connected database. Use only an explicitly designated test database.

```bash
read -rsp 'Test PostgreSQL URL: ' CYBERRANGE_SETUP_TEST_DATABASE_URL
echo
export CYBERRANGE_SETUP_TEST_DATABASE_URL
export CYBERRANGE_SETUP_TEST_DATABASE_NAME=cyberrange_setup_test
.venv/bin/python backend/tests/integration/test_admin_setup_postgres.py -v
unset CYBERRANGE_SETUP_TEST_DATABASE_URL CYBERRANGE_SETUP_TEST_DATABASE_NAME

python3 -m unittest discover -s backend/tests -p test_admin_setup.py -v
git diff --check
```

The integration test invokes the real `scripts/admin_setup.py` CLI using the
same interpreter and passes the database URL and random passwords through child
process environment variables. Its output reports server version, database name,
CLI count, initial emptiness and retained fixture prefix without credentials.

Separately, all 11 existing setup unit tests passed. They use doubles and are
not PostgreSQL integration evidence. Running the integration file without its
explicit environment variables correctly skips the check.

Observed dependency/deprecation warnings concerned LangChain and
`datetime.utcnow()`; neither failed these checks. The full backend/frontend
suites were not run for this test-and-report change.
