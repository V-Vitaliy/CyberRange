# Running locally with Groq

CyberRange is a learning project with intentional vulnerabilities. This document describes how to run the existing application manually; the commands are not executed automatically. Table, user, and session setup is implemented in `scripts/admin_setup.py`; learning documents/CTF and isolated SQLi remain separate tasks.

## 1. Environment

Run commands from the repository root. Use Python 3.11 or later. The frontend requires a Node.js version compatible with the existing Next.js version; the frontend checks for these instructions were run on Node.js 24.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-groq.txt
```

`requirements-groq.txt` contains the existing application dependencies without `llama-cpp-python`. The full `requirements.txt` includes this list and local llama-cpp support. This lets you start with Groq without compiling a local LLM. Embeddings are still computed by the existing SentenceTransformer on CPU; Groq replaces only text generation.

If `.env` does not exist yet, copy `.env.example` to `.env`. Do not overwrite your own settings. Configure:

- `GROQ_API_KEY`: your key, stored only in the local `.env`.
- `JWT_SECRET`: your own random secret.
- `DATABASE_URL`, `REDIS_URL`: local connections consistent with your database setup.
- `CHROMA_HOST=localhost`, `CHROMA_PORT=8000`.
- `SMTP_HOST=`: leave empty for the first run. The email tool will simulate delivery and explicitly report that no email was sent.

## 2. Services and document processing data

Existing Compose files:

```bash
docker compose -f docker-compose.state.yml up -d
docker compose -f docker-compose.compute.yml up -d chromadb
python -m nltk.downloader punkt_tab
```

Ports: PostgreSQL 5432, Redis 6379, Chroma 8000, API 8001, frontend 3000. The current PDF upload flow does not require MinIO: files are stored locally. Its Compose service is retained.

On first use, SentenceTransformer downloads `all-mpnet-base-v2`, and tiktoken downloads `cl100k_base` data; the purchased reranker downloads `cross-encoder/ms-marco-MiniLM-L-6-v2`. This requires network access or a populated cache. Groq does not require GGUF.

### Preparing tables and initial records

Seeding requires only a running PostgreSQL instance and backend dependencies; it does not require Redis, Chroma, Groq, or the frontend. The PostgreSQL database must already exist (the PostgreSQL Compose image creates it on first startup). The script does not start containers or create `.env`.

From the repository root, in the prepared Python environment:

```bash
python scripts/admin_setup.py --lab-id 65319dde-b9fd-4aa2-9988-1467f46100a1
```

This is an example of a stable lab UUID. For another lab, choose a different UUID and different usernames. On subsequent runs, use **the same** UUID and usernames; generate a UUID once with `python -c 'import uuid; print(uuid.uuid4())'`.

- `DATABASE_URL` comes from the existing backend settings: the environment variable takes precedence over the root `.env`, followed by application defaults. Format: `postgresql+asyncpg://USER:PASSWORD@HOST:5432/DB`; special characters in URL credentials require percent-encoding.
- New passwords are requested using hidden input. For noninteractive runs, set `RED_TEAM_PASSWORD` and `BLUE_TEAM_PASSWORD` **in the process environment**. The script does not read these two variables from `.env`. Passwords are not passed as CLI arguments or printed; they are stored using the existing `get_password_hash`. Whitespace is significant. Existing users' passwords are neither requested nor changed.
- Default usernames: `red_student` and `blue_analyst`. Override them with `--red-username` and `--blue-username`.
- For a new session: `--budget 5`; `--is-solo` is enabled by default, and `--no-is-solo` disables Auto-SOC. Repeated setup preserves the existing budget, defenses, prompt, and `is_solo`. Seeding itself does not start SOC; it runs after API startup with a model that supports it.
- Both users and the session share `lab_instance_id`; there is no separate lab table. `session_id` is the game session UUID. A new session's `team_id` defaults to the red user's UUID; set it explicitly with `--team-id`. `User` has no `team_id` field. An existing session retains its team; a conflicting explicit `--team-id` is rejected.
- Output is JSON containing lab, session, team, and user IDs, plus `created` indicators. After login, the dashboard gets its session from `/api/blue/me`; Red Team receives a token with a signed `lab_scope` through the existing `/api/red/login`.

Repeat the same command to check idempotency: passwords, UUIDs, budget, defenses, and progress are preserved. A missing user can be added to the same lab. A username taken by another role/lab, identical red/blue usernames, multiple sessions in one lab, or a team conflict causes failure without reassigning records.

The script uses `Base.metadata.create_all(checkfirst=True)` and one transaction for DDL and seeding. It **does not migrate** an existing schema, delete tables, or change investigation/CTF data. A PostgreSQL advisory lock serializes concurrent runs of this script, including table creation. It does not restrict normal application requests. Timeouts: connection 10 seconds, lock wait 10 seconds, SQL statement 30 seconds.

The connection requires `CONNECT` on the database, `USAGE` and `CREATE` on the target schema (usually `public`), and read/insert permissions on `users`/`game_sessions`. Creating missing tables also requires appropriate permissions on the types and referenced tables. For a clean local database, the owner role from Compose is sufficient. This is an administrative connection, not the connection for future SQLi exercises. An incompatible existing schema causes failure: schema repair is separate, with no automatic reset.

Failures produce a short message without the DSN, passwords, or SQL parameters, and a nonzero exit code. If the connection is lost during commit, the outcome may be unknown: after recovery, repeat the command with the same parameters. This script does not yet load learning documents or flags.

## 3. Starting the API and dashboard

In the first terminal, from the repository root with the Python environment activated:

```bash
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8001
```

The working directory affects where uploaded files are stored. Use the same directory on subsequent runs.

In a second terminal:

```bash
cd frontend
npm ci
npm run dev
```

By default, the dashboard calls `http://127.0.0.1:8001/api/blue`. For a different address, set `NEXT_PUBLIC_BLUE_API_URL` in `frontend/.env.local` using `frontend/.env.example` as a guide, then restart Next.js. For a production build, set this address before `npm run build`.

- Dashboard: `http://localhost:3000/blue-team/dashboard`.
- Red Team API/Swagger: `http://127.0.0.1:8001/api/red/docs`.
- Blue Team API/Swagger: `http://127.0.0.1:8001/api/blue/docs`.
- Basic health: `http://127.0.0.1:8001/api/health`; this alone does not establish that all services work.

After login, the dashboard gets the session ID from `/api/blue/me`. The `?session_id=...` parameter is still supported; the backend checks session ownership. If no session exists yet, the dashboard displays a message asking for database setup.

Red Team uses the API. For protected requests, pass `Authorization: Bearer <access_token>` using the token returned by login. There is no separate browser-based Red Team chat yet. Old tokens without `lab_scope` require a new login.

## 4. Checks before integration startup

Isolated seed unit tests use the standard library and need no `.env`, network, or installed backend dependencies:

```bash
python3 -m unittest discover -s backend/tests -p test_admin_setup.py -v
```

They check logic using storage, transaction, and hashing doubles. This suite does not test real PostgreSQL, bcrypt, SQLAlchemy, or login routes. A separate opt-in integration check covers those; commands and results on PostgreSQL 15.19 are in [ISSUE_58_VALIDATION.md](ISSUE_58_VALIDATION.md). The remaining tests require the existing `requirements-test.txt`:

```bash
python -m pip install -r backend/requirements-test.txt
PYTHONPATH=backend python -m pytest -q -ra backend/tests
cd frontend
node --test tests/blue-team-api.test.mjs
npx tsc --noEmit --incremental false
npm run build -- --webpack
```

The Node test command uses built-in TypeScript support in Node.js 24 and requires no new test package. Backend tests do not send real email or call a cloud model. They check real LangChain streaming structures and tool schema generation through the real Groq adapter, while model responses and email delivery are mocked.

## 5. Checks after database/data tasks are complete

1. Log in as Blue Team and confirm the dashboard receives the real session and budget.
2. Log in as Red Team and ask a question through `/api/red/chat/ask` using the real `session_id` and a new `thread_id` UUID. Check the SSE response and SIEM event.
3. Ask a question about a public document in your lab.
4. Request a test email to an address in the `.invalid` domain, leaving `SMTP_HOST` empty. Check the simulation message and `AI_EMAIL_SIMULATED` event with `delivery=simulated`. An educational flag leak remains an `AI_DATA_EXFILTRATION` event, also marked with the delivery mode.
5. Check manual investigation of benign and malicious events. Auto-SOC in a solo session may investigate an event before the user; the API should report this correctly.
6. Purchase a defense and check budget deduction, button state, and the corresponding API behavior. In particular, JWT role forgery should work only before the defense is enabled.
7. Confirm that a Groq failure leaves the event uninvestigated and that the dashboard displays API errors, including token expiry.

The full scenario with real services has not yet been confirmed. Database setup and login were checked against a separate test PostgreSQL database for Issue #58; that check did not run the full API lifecycle. Learning data and SQLi/CTF are not ready yet. Containers, the database, and `.env` were not created as part of the seed implementation.
