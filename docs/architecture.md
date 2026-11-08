# Architecture

CyberRange is a vulnerable-by-design lab for attacks on and defenses of LLM
applications. A Red Team attacks a RAG assistant with tools; a Blue Team watches
a SIEM, investigates events and buys defenses. This page describes how the
platform is built. Exercises are listed in [exercises.md](exercises.md).

## Components

```
 Browser (Blue dashboard, Next.js :3000)      Red Team client (API / Swagger)
                 │                                         │
                 ▼                                         ▼
 ┌──────────────────────── FastAPI backend :8001 ───────────────────────────┐
 │  /api/blue (blue_app)      /api/red (red_app, incl. /ctf)    /api/health │
 │  background: Auto-SOC worker, ETL jobs                                   │
 └───┬──────────┬───────────┬──────────────┬─────────────┬───────────┬──────┘
     ▼          ▼           ▼              ▼             ▼           ▼
 PostgreSQL   Redis      Envoy :8000 ──► Chroma     SeaweedFS S3   LLM server
 :5432        :6379      (token proxy)  (vectors)   :8333          (OpenAI-compatible,
 (state, SIEM)(rate limit, ETL status)              (lab uploads)   Groq or local GGUF)
                                                                    Mailpit :1025 (email sink)
```

- **State** (`docker-compose.state.yml`): PostgreSQL and Redis.
- **Compute** (`docker-compose.compute.yml`): Chroma behind the Envoy token proxy,
  SeaweedFS (S3 gateway only) with a one-shot bucket init job, Mailpit.
- All published ports bind to `127.0.0.1` by default (`STATE_BIND_ADDRESS`,
  `COMPUTE_BIND_ADDRESS`); every service requires credentials from `.env`, and
  Compose refuses to start without them.

## Lab isolation boundary

A **lab instance** (`lab_instance_id`, a UUID) is the unit of isolation. Users,
the game session, chat threads, SIEM events, CTF flags, Chroma documents and
uploaded objects all carry the lab ID.

- Intentional vulnerabilities must stay inside the attacker's own lab. Reaching
  another lab, the control-plane database, infrastructure services, the host or
  the internet is a platform bug.
- The server enforces the boundary; it never relies on client input:
  - identity and lab come from the **signed** `lab_scope` claim, not from
    forgeable token fields;
  - every session lookup goes through `labs.service.get_authorized_session`
    (session must belong to the caller's lab);
  - Chroma queries always filter by `lab_instance_id`;
  - object keys always start with the caller's lab ID;
  - the SQLi exercise runs as a per-lab, read-only PostgreSQL role that sees only
    `security_barrier` views filtered by the lab ID (`chat/sqli_isolation.py`);
  - lab IDs are identifiers, not secrets: a lab ID from request input never
    proves that the caller belongs to the lab (login uses the signed lab code).

## Authentication and roles

Implemented in `auth/` (`service.py`, logins in `router.py`).

- Roles: `red_team`, `blue_team`, `admin`. Passwords are bcrypt hashes.
- Login (`POST /login` on both teams) takes `username`, `password` and
  `lab_code`. The code is the lab ID signed with `JWT_SECRET`
  (`labs.service.lab_login_code`), printed by `admin_setup` with the
  credentials. Lab IDs are not secret (the SQLi exercise can list every lab's
  database role), so a bare or forged code gets 401 without a lookup or event.
  The user is looked up only inside the code's lab; after login the lab comes
  from the token, as below.
- A failed login is logged as `LOGIN_FAILED` in that lab (username, portal,
  source IP and how many distinct usernames failed from that source in the last
  5 minutes; never the password or whether the username exists). Counting and
  writing share one transaction under an advisory lock per lab and source, so
  concurrent failures count each other.
- Login Hardening, the shop defense for exercise EX-07, protects login against
  account enumeration (`auth.service.authenticate`; how is in the instructor
  notes) and lets each source try at most 10 logins per minute in the lab (429
  and one `LOGIN_RATE_LIMITED` event per window; one Redis key per minute, set
  with INCR and EXPIRE in one transaction; not limited while Redis is down).
- `create_access_token` issues an HS256 JWT signed with `JWT_SECRET` (startup
  fails if it is weak or published). Red Team tokens carry a signed `lab_scope`.
- `get_current_user_secure` verifies the signature (Blue Team).
- `get_current_user_vulnerable` (Red Team, including CTF submit) reads the `role` claim without
  verifying it, but takes identity and lab from `lab_scope`; this is an
  intentional exercise. The "JWT Strict Mode" defense switches it to full
  verification.
- A claimed role that differs from the stored one is logged as
  `AUTH_BYPASS_ATTEMPT` (claimed role, source IP, `blocked` when Strict Mode
  rejects it) in the student's own lab, at most once per user, role and outcome
  per 15 minutes (de-duplicated in Redis; logged every time if Redis is down).

## Request flows

- **Chat** (`POST /api/red/chat/ask`, SSE): authorize session → optional rate
  limit → `PromptBuilder` → the request's transaction ends (`db.rollback()`) →
  agent turn (`chat/llm_engine.run_agent_turn`) → history saved. Nothing is
  retrieved in advance: the prompt tells the model to look up university
  answers with the `search_documents` tool. The agent turn is a loop: the
  model streams its answer or tool calls; each tool call runs through the
  tool registry (strict argument validation, one SIEM event per action) and
  its result goes back to the model as a `ToolMessage` with the same
  `tool_call_id`; the loop repeats until the model answers in text. A tool
  that fails is logged as `AI_TOOL_FAILED` and reported to the model as a
  generic failure; details go to the server log. At most `AGENT_MAX_STEPS` model calls run per turn; after
  that the turn ends with a generic answer. Every executed tool call is sent
  over SSE as `{"step": {"step", "tool", "arguments", "outcome"}}` (values
  truncated to `AGENT_LOG_VALUE_CHARS`) next to the `{"text": ...}` tokens;
  only the final text is saved in the history. A turn that called tools or hit
  the limit ends with one `AGENT_TURN` SIEM event (`status`: `answered`,
  `step_limit` or `error`, and the steps). Errors return a generic SSE
  message; details go to the server log.
- **Tool context**: `run_agent_turn` passes a `ToolContext` (lab, role from the
  token, the session's reranker flag, vector store) through
  `execute_tool_call` to every tool handler. `search_documents` queries Chroma
  filtered by that lab (public documents only unless `admin`), applies the
  reranker when the session bought it, returns at most
  `DOCUMENT_SEARCH_MAX_CHUNKS` chunks and `DOCUMENT_SEARCH_MAX_CHARS`
  characters, and logs `AI_DOCUMENT_SEARCH` with the query and chunk count.
- **Upload** (`POST /api/red/upload`): size and PDF magic-byte checks → object key
  resolved under the lab prefix → stored in S3 → files in `student_uploads/` are
  indexed by a background ETL job; progress is in Redis and streamed over
  `/api/red/ws/etl-status/{task_id}`.
- **SIEM**: `siem.service.log_security_event` writes lab-scoped events
  to `security_audit_logs` in its own database session, so a failed write never
  commits or rolls back the request's transaction. These sessions come from a
  separate connection pool (`db.database.siem_engine`): a request that holds a
  connection while logging never waits for another one from its own pool, so
  concurrent requests cannot deadlock the request pool. The Blue Team reads them;
  `siem/forensics.py` scores investigations.
- **Auto-SOC** (`siem/soc_worker.py`): for solo labs, polls uninvestigated
  events, asks the LangGraph SOC agent (`agents/soc_agent.py`) for a structured
  verdict and saves it in a short transaction. No transaction is open while the
  model runs. An unreachable model pauses analysis with backoff. Login events
  (`LOGIN_FAILED`, `LOGIN_RATE_LIMITED`) never reach the model: each poll closes
  them in bulk with the `siem/forensics.py` rule (5 or more distinct usernames
  from one source is an enumeration). A `LOGIN_FAILED` whose count could not be
  taken stays pending.
- **Defenses** (`defense/service.py`): Blue Team spends points to enable
  per-session switches (system prompt patch, rate limiter, reranker, JWT strict
  mode, login hardening).

## Concurrency model

- One uvicorn process. The event loop only waits on I/O: asyncpg, redis.asyncio,
  HTTP to the model, SSE and WebSocket.
- Blocking or CPU work never runs on the loop:
  - default thread pool (`asyncio.to_thread`): bcrypt at login, query embedding
    and Chroma search, reranker, SMTP, S3 calls;
  - dedicated ETL pool (`ETL_MAX_WORKERS`, threads `etl-*`): PDF parsing,
    chunking, document embeddings and Chroma writes, so uploads cannot starve
    chat and login.
- Chat and Auto-SOC share one model through `LLMQueueManager` (an asyncio lock).
  A chat turn holds the lock only during each model call, not while its tools
  run, so other chats and the Auto-SOC can use the model between agent steps.
- A chat request ends its database transaction before the agent turn starts,
  so a turn of several model calls does not hold a pooled connection; the
  history is saved in a new short transaction at the end.

## Backend module map (`backend/app/`)

| Package | Contents |
|---|---|
| `main.py` | App, mounted `red_app` / `blue_app`, lifespan (secret check, stores, model, SOC task), CORS, body size limit |
| `core/` | `config.py` (settings), `queue_manager.py`, `redis_client.py` |
| `llm/` | `provider.py`: model factory (OpenAI-compatible, Groq, local GGUF) |
| `agents/` | `soc_agent.py` (LangGraph SOC analyst), `tools/` (tool registry: schema, risk level, audited handler with the turn context; `send_email` with `email_service.py`, `search_documents` with the reranker) |
| `db/` | `models.py`, `database.py`, `ids.py` (`ensure_uuid`) |
| `auth/` | Users and login: `repository.py` (`UserRepository`), `service.py` (passwords, JWT, `authenticate`, `get_current_user_secure`, `get_blue_team_user`, `get_current_user_vulnerable`, `dummy_password_hash`), `router.py` (Red and Blue `POST /login`, Blue `GET /me`), `schemas.py` |
| `chat/` | Red Team chat: `repository.py` (`ChatRepository`), `service.py` (chat turn and SSE, thread lookup, history search), `llm_engine.py` (agent loop: model steps and tool calls), `prompt_builder.py`, `sqli_isolation.py` and `sqli_search.py` (lab-isolated SQLi exercise), `router.py` (`/chat/ask`, `/chat/history`), `schemas.py` |
| `ctf/` | Flag submission: `repository.py` (`CtfRepository`), `service.py` (`hash_ctf_flag`, `submit_flag`), `router.py` (`POST /api/red/ctf/submit`), `schemas.py` |
| `defense/` | Defense shop and rate limit: `service.py` (`process_defense_purchase`, `enforce_rate_limit`, `enforce_login_attempt_limit`), `router.py` (`POST /api/blue/defenses/buy`), `schemas.py` |
| `documents/` | Uploads and RAG documents: `service.py` (`ETLWorker`: PDF parsing, chunking, embedding), `object_store.py` (S3 uploads, lab-prefixed keys), `chroma_client.py` (`VectorStore`), `router.py` (`POST /api/red/upload`, `/api/red/ws/etl-status/{task_id}`) |
| `labs/` | Game sessions and exercise seeding: `repository.py` (`GameSessionRepository`, `LabSecretRepository`), `service.py` (`get_session`, `get_session_for_lab`, `get_authorized_session`, `get_exercise_secret`, `lab_login_code`), `exercises.py` (per-lab flags for EX-03/04/05, used by `scripts/seed_exercises.py`) |
| `siem/` | SIEM events: `repository.py` (`AuditLogRepository`), `service.py` (`log_security_event` for every domain, dashboard stats, logs), `forensics.py` (Blue Team investigation), `soc_worker.py` (Auto-SOC loop), `router.py` (`/investigate`, `/dashboard`, `/logs`), `schemas.py` |

Each domain package (`app/<domain>/`) holds its `router.py`, `service.py`,
`repository.py` and `schemas.py` as needed. Dependencies point inward: routers →
services → repositories. Other domains use a domain's `service`, never its
`repository`; `core/` imports no repository; each route is declared in its
domain's router. `backend/tests/unit/test_architecture.py` enforces these rules.

## Configuration and secrets

- Settings come from `.env` (`core/config.py`); `.env.example` lists every
  variable with placeholders.
- There are no migrations. Scripts create missing tables and apply
  `db.database.SCHEMA_UPGRADES` (idempotent `ALTER TABLE` for columns added
  later); run `scripts/admin_setup.py` once after upgrading.
- Lab codes are signed with `JWT_SECRET`: rotating it invalidates every issued
  lab code (and token); rerun `admin_setup` to print the new codes.
- No usable defaults for secrets: `JWT_SECRET`, database and Redis passwords,
  `CHROMA_AUTH_TOKEN`, `S3_ACCESS_KEY` / `S3_SECRET_KEY`. The backend or Compose
  refuses to start without them.
- Agent email is delivered only to `EMAIL_ALLOWED_DOMAINS` (default `lab.local`),
  normally into Mailpit.

## Tests and CI

- `backend/tests/unit`, `backend/tests/api`: mocked, fast, run on every push.
- `backend/tests/integration`: real PostgreSQL (admin setup, SQLi isolation, SOC
  transactions); in CI they must run, not skip.
- `compose-isolation` CI job starts the real Compose services and checks
  credentials, loopback binding, the Chroma proxy, S3 permissions and Mailpit.
- `frontend`: Node tests and type check. Ruff lint and format check.
