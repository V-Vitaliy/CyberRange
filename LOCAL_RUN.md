# Running locally with Groq or an OpenAI-compatible server

CyberRange is a learning project with intentional vulnerabilities. This document describes how to run the existing application manually; the commands are not executed automatically. Table, user, and session setup is implemented in `scripts/admin_setup.py`; isolated SQLi preparation is described below. Exercise flags are seeded by `scripts/seed_exercises.py`.

## 1. Environment

Run commands from the repository root. Use Python 3.11 or later. The frontend requires a Node.js version compatible with the existing Next.js version; the frontend checks for these instructions were run on Node.js 24.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-base.txt
```

| File | Use |
| --- | --- |
| `requirements-base.txt` | Application runtime for the OpenAI-compatible HTTP and Groq providers. Installs CPU-only torch: embeddings and the reranker run on CPU. |
| `requirements.txt` | Base runtime plus `llama-cpp-python` for in-process GGUF inference. |
| `requirements-test.txt` | Service-free test suite; no model weights or torch. |

HTTP generation does not require compiling a local LLM. An existing environment with CUDA torch keeps working; recreate it to reclaim the space.

If `.env` does not exist yet, copy `.env.example` to `.env`. Do not overwrite your own settings. Configure:

- `GROQ_API_KEY`: your key, stored only in the local `.env`.
- `JWT_SECRET`: required, generate with `openssl rand -hex 32`. The API refuses to start with an empty value, a value shorter than 32 bytes, or a published example value: a known secret lets lab users forge Blue Team tokens and other labs' scopes.
- `DATABASE_URL`, `REDIS_URL`: local connections consistent with your database setup.
- `CHROMA_HOST=localhost`, `CHROMA_PORT=8000`, `CHROMA_AUTH_TOKEN` (generate with `openssl rand -hex 32`).
- `S3_ENDPOINT_URL=http://localhost:8333`, `S3_BUCKET=lab-uploads`, `S3_ACCESS_KEY` (`openssl rand -hex 16`) and `S3_SECRET_KEY` (`openssl rand -hex 32`).
- `SMTP_HOST=`: leave empty for the first run. The email tool will simulate delivery and explicitly report that no email was sent. To see real messages, start the Mailpit sink (`docker compose -f docker-compose.compute.yml up -d mailpit`), set `SMTP_HOST=localhost`, `SMTP_PORT=1025`, `SMTP_STARTTLS=false`, and open http://localhost:8025. With any SMTP server, the agent can only email domains in `EMAIL_ALLOWED_DOMAINS` (default `lab.local`); other recipients are blocked and logged as `AI_EMAIL_BLOCKED`, or `AI_DATA_EXFILTRATION` when the message contains a flag.

For an OpenAI-compatible inference server, configure these values in your local
`.env` instead of using a Groq key as a local-server switch. Replace the URL and
model placeholders with the server's base URL and model ID:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://<host>:<port>/v1
LLM_MODEL=<model-id>
LLM_API_KEY=
LLM_MAX_TOKENS=2048
```

The explicit provider takes precedence over `GROQ_API_KEY`. The adapter uses
`POST /v1/chat/completions` and streams chat. An empty API key uses a dummy local
key required by the client; set `LLM_API_KEY` if your server requires authentication.
Without `LLM_PROVIDER` (or with an empty value), the previous behavior remains:
a nonempty `GROQ_API_KEY` selects Groq with `llama-3.1-8b-instant`; otherwise the
in-process GGUF path and existing hardware settings apply. No Groq URL or payload
rewriting is used. Restart the API after changing configuration.

`LLM_MAX_TOKENS` applies only to the OpenAI-compatible provider and includes
reasoning tokens. With the user's schedule PDF, the configured model exhausted the previous
512-token budget on `reasoning_content` without producing an answer; 2048
produced visible text and a normal `stop`. Empty replies and `length` termination
now surface as SSE errors and are not saved as successful chat turns. Increase
the configured budget if a longer request still reaches its limit. Groq/GGUF
retain their existing 512-token defaults.

Streaming through the configured server, email tool calls (SMTP disabled), and SOC function-call output
were checked separately against this server. SOC sends one schema tool with
`tool_choice=required`, uses a complete nonstreaming response, and rejects malformed
JSON, incomplete calls and nonboolean verdicts. Chat keeps streaming and tools.
Failures propagate; the worker leaves unsuccessful analysis pending. These checks
do not establish real Groq compatibility. Service checks and remaining blockers
are recorded in issue #61.

### Local CLI probe (delivered separately, ignored by Git)

From the primary repository root:

```bash
.venv/bin/python .local-tools/rag_probe.py
```

It defaults to the application API on `8001`, asks for Red Team login (password
hidden), a session UUID and questions, and creates one new thread for repeated
questions. It shows redacted requests, HTTP statuses and joined Unicode SSE
answers, including errors embedded in HTTP 200. Use `--base-url http://127.0.0.1:8002`
only when deliberately targeting the separately configured test API.

PDF upload is optional and requires an explicit path and confirmation. Ordinary
uploads do not index; the current lab exercise indexes multipart filenames
`../student_uploads/<basename>` within the authenticated lab. An upload response
does not establish ETL completion; check `/api/red/ws/etl-status/{task_id}` with
the same authenticated identity. Existing filenames can be overwritten by the
API, so choose the path deliberately. The helper never uploads automatically or
saves responses/credentials. `.local-tools` is ignored and is not delivered by
a commit or PR; retain the separately supplied local script.

## 2. Services and document processing data

Existing Compose files:

```bash
docker compose -f docker-compose.state.yml up -d
docker compose -f docker-compose.compute.yml up -d chroma-proxy seaweedfs seaweedfs-init
python -m nltk.downloader punkt_tab
```

Ports: PostgreSQL 5432, Redis 6379, Chroma token proxy 8000, S3 gateway 8333, API 8001, frontend 3000.

PostgreSQL, Redis and Chroma are published only on `127.0.0.1` by default
(`STATE_BIND_ADDRESS`, `COMPUTE_BIND_ADDRESS`). Lab users must reach only the API
and frontend: direct access to these services would bypass lab isolation. For a
split deployment, bind them to a private address that lab users cannot reach.
Redis requires `REDIS_PASSWORD`. Chroma 1.x has no built-in authentication, so
the Chroma container publishes no port: an Envoy proxy (`chroma-proxy`,
`deploy/envoy-chroma.yaml`) is the only entry point and rejects requests without
`Authorization: Bearer <CHROMA_AUTH_TOKEN>` with 403. The backend refuses to start
without the token. The proxy also caps every Chroma call at 60 seconds.

Lab uploads are stored in SeaweedFS (`seaweedfs`, `deploy/seaweedfs-s3.json`)
under `<lab_instance_id>/uploads/` and `<lab_instance_id>/student_uploads/` in the
`S3_BUCKET` bucket; only `student_uploads/` is indexed. Only the S3 gateway (8333)
is published. Its single identity uses `S3_ACCESS_KEY`/`S3_SECRET_KEY` and may only
read, write and list that bucket; anonymous requests are denied. The one-shot
`seaweedfs-init` service creates the bucket. The backend refuses to start without
the keys or the bucket. The old local `lab_uploads/` folder is no longer used.

Input limits (optional `.env` overrides): `UPLOAD_MAX_BYTES` (10 MiB, PDF files
only), `PDF_MAX_PAGES` (200) and `PROMPT_MAX_CHARS` (4000). Requests that declare
a larger body are rejected before they are read. A chunked request without
`Content-Length` is still bounded by the upload route itself; in a public
deployment also cap the body size in the reverse proxy.

Upgrading an existing installation:

- Set `REDIS_PASSWORD` and add it to `REDIS_URL`, then recreate Redis with
  `docker compose -f docker-compose.state.yml up -d redis`.
- `POSTGRES_PASSWORD` only applies when the data volume is first created. If
  your volume was created with the old default password, change it:
  `docker exec -it cyberrange_postgres psql -U cyberadmin -d cyberrange -c "ALTER USER cyberadmin PASSWORD '<new password>'"`,
  then update `POSTGRES_PASSWORD` and `DATABASE_URL`.

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

CTF flag ownership uses the existing `ctf_flags.lab_id` String column: its value
must be the canonical UUID string of the owning `GameSession.lab_instance_id`
(lowercase, with hyphens), not a scenario name or session/team UUID. The submit
route looks up both the SHA-256 hash and the lab from the server-validated user
identity. The same hash may be registered separately in different labs, each
with its own flag row and reward. Unknown and foreign flags receive the same
`400 Invalid flag.` response; successful submissions retain the session's team
and the authenticated lab. This rule does not alter the schema or rewrite any
existing flags. Legacy scenario identifiers or other noncanonical lab values
will not match; inspect existing data and agree on any mapping separately before
changing those records. `admin_setup` itself does not create educational flags;
see [Seeding exercise flags](#seeding-exercise-flags).

The connection requires `CONNECT` on the database, `USAGE` and `CREATE` on the target schema (usually `public`), and read/insert permissions on `users`/`game_sessions`. Creating missing tables also requires appropriate permissions on the types and referenced tables. For a clean local database, the owner role from Compose is sufficient. This is an administrative connection, not the connection for future SQLi exercises. An incompatible existing schema causes failure: schema repair is separate, with no automatic reset.

Failures produce a short message without the DSN, passwords, or SQL parameters, and a nonzero exit code. If the connection is lost during commit, the outcome may be unknown: after recovery, repeat the command with the same parameters. This script does not load learning documents or flags.

### Seeding exercise flags

After `admin_setup`, seed the scored flags of one lab. Unlike `admin_setup`, this
step needs the running stack: PostgreSQL, Redis, Chroma and the embedding model.

```bash
python scripts/seed_exercises.py --lab-id 65319dde-b9fd-4aa2-9988-1467f46100a1
```

- It creates a random `flag{...}` per exercise, stored only as a SHA-256 hash in
  `ctf_flags` for this lab:
  - **EX-03**: a secret in the assistant's system context; it stays there after
    Prompt Patch. Its plaintext is kept in `lab_secrets` because the model needs it.
  - **EX-04**: a chat thread of a hidden `staff_<lab UUID hex>` user (role `staff`, random
    password that is never stored or shown), reachable only through the SQLi views.
  - **EX-05**: a one-page PDF indexed by the upload pipeline with
    `access_level=private`; retrieval returns it only to the `admin` role. Its
    plaintext is kept in `lab_secrets`, so the document can be rebuilt.
- The output JSON lists the new flag values under `created`. **They are printed
  only once**: store them for grading and never share them with students.
- Flags are committed first; the private PDF is indexed only afterwards, so the
  vector store never holds a flag the database does not know. If indexing fails,
  the new values are still printed and the exit code is nonzero: repeat the
  command. Every run replaces the lab's private PDF (old chunks removed first)
  instead of adding a copy.
- Repeating the command creates only missing flags and prints no values for
  existing ones. A PostgreSQL advisory lock serializes concurrent runs, including
  the indexing.
- Missing tables (`lab_secrets` on older databases) are created; existing ones
  are not migrated.

### Preparing the educational SQLi search

Do this explicitly for each existing lab, using an administrative connection to
the same database as the API:

```bash
.venv/bin/python scripts/provision_sqli.py --lab-id <existing-lab-uuid>
```

The command asks for a separate reader password (at least 24 characters), creates
restricted roles and protected views, and outputs their names. Save that password
locally; existing objects are never overwritten or rotated automatically. The
reader can search its lab's chat history and discover its audit events through
SQLi. It cannot read source tables, other labs, users or defense settings. This
does not create documents or flags, and does not start any services.

Create a local secret JSON mapping each canonical lab UUID to its reader URL:

```json
{
  "<lab-uuid>": "postgresql+asyncpg://sqli_read_<uuid-without-hyphens>:<percent-encoded-reader-password>@localhost:5432/cyberrange"
}
```

For example, store it at `.local-sqli59/readers.json` (gitignored), with directory
permissions 0700 and file permissions 0600. Replace the placeholders locally;
never commit the populated file. The host, port and database must match
`DATABASE_URL`. Then configure the API environment or local `.env`:

```dotenv
SQLI_CONNECTIONS_FILE=.local-sqli59/readers.json
```

Relative file paths resolve from the repository root. Restart the API after
changing the setting. Missing/invalid lab configuration returns 503 with a
`SQL_ERROR` event and never falls back to the administrative connection.
Injectable queries use a separate reader connection, which is closed after each
request; a syntax/permission error returns 400 and does not break SIEM or the
next request. The response remains `{"results": [...]}` with id/title/messages
for each row. Normal search matches titles and serialized messages.

Example `q` values: `Welcome` for a normal search, `' OR TRUE --` to bypass its
condition, and `' AND FALSE UNION ALL SELECT id,event_type,payload FROM audit_logs --`
to discover own-lab audit events. Authorization still rejects a foreign session
before the search starts. SQL errors include safe SQLSTATE descriptions; driver
exceptions, connection credentials and data values are not copied into errors.

The separately prepared demo uses `cyberrange_setup_test`; do not point the
ordinary application's database configuration there accidentally. Existing demo
connections are in `.local-sqli59/connections.json`, and its runtime map is
`.local-sqli59/readers.json`. The production lab needs its own provisioned views
and reader credentials, prepared with `scripts/provision_sqli.py`. The isolation
guarantees are verified by `backend/tests/integration/test_sqli_isolation_postgres.py`.

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

In Swagger, first call `/login`, copy only the returned `access_token` value,
then click **Authorize** and paste that token **without** the `Bearer` prefix.
The HTTP Bearer scheme adds the prefix and sends the header automatically.
Authorize separately in the Red and Blue sub-app documentation using the
appropriate user. An ordinary OpenAPI header parameter named `Authorization`
does not work in Swagger; protected endpoints use the Bearer security scheme.

## 4. Checks before integration startup

Isolated seed unit tests use the standard library and need no `.env`, network, or installed backend dependencies:

```bash
python3 -m unittest discover -s backend/tests -p test_admin_setup.py -v
```

They check logic using storage, transaction, and hashing doubles. This suite does not test real PostgreSQL, bcrypt, SQLAlchemy, or login routes. A separate opt-in [PostgreSQL setup test](backend/tests/integration/test_admin_setup_postgres.py) covers those; see [backend test instructions](backend/tests/README.md) for its required environment variables. The remaining tests require the existing `requirements-test.txt`:

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

The checks above are a manual checklist, not a claim that every scenario passed.
The issue #61 verification report distinguishes actual
model/PostgreSQL/Redis/Chroma/API checks from offline tests and blocked PDF/CTF/Groq
scenarios. Learning data and CTF flags are not prepared by setup. Never use a
working-directory change as database isolation: select a test database explicitly
before purchases, investigations or other modifying checks.
