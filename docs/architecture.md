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
  - every session lookup goes through `get_authorized_session` (session must
    belong to the caller's lab);
  - Chroma queries always filter by `lab_instance_id`;
  - object keys always start with the caller's lab ID;
  - the SQLi exercise runs as a per-lab, read-only PostgreSQL role that sees only
    `security_barrier` views filtered by the lab ID (`db/sqli_isolation.py`).

## Authentication and roles

- Roles: `red_team`, `blue_team`, `admin`. Passwords are bcrypt hashes.
- `create_access_token` issues an HS256 JWT signed with `JWT_SECRET` (startup
  fails if it is weak or published). Red Team tokens carry a signed `lab_scope`.
- `get_current_user_secure` verifies the signature (Blue Team).
- `get_current_user_vulnerable` (Red Team, including CTF submit) reads the `role` claim without
  verifying it, but takes identity and lab from `lab_scope`; this is an
  intentional exercise. The "JWT Strict Mode" defense switches it to full
  verification.

## Request flows

- **Chat** (`POST /api/red/chat/ask`, SSE): authorize session → optional rate
  limit → Chroma search filtered by lab (public documents only unless `admin`)
  → optional reranker → `PromptBuilder` → model stream through the shared queue
  → tool calls via the tool registry → history saved. Errors return a generic
  SSE message; details go to the server log.
- **Upload** (`POST /api/red/upload`): size and PDF magic-byte checks → object key
  resolved under the lab prefix → stored in S3 → files in `student_uploads/` are
  indexed by a background ETL job; progress is in Redis and streamed over
  `/api/red/ws/etl-status/{task_id}`.
- **SIEM**: `services/siem_logger.log_security_event` writes lab-scoped events
  to `security_audit_logs`. The Blue Team reads them; `services/forensics.py`
  scores investigations.
- **Auto-SOC** (`services/soc_worker.py`): for solo labs, polls uninvestigated
  events, asks the LangGraph SOC agent (`agents/soc_agent.py`) for a structured
  verdict and saves it in a short transaction. No transaction is open while the
  model runs. An unreachable model pauses analysis with backoff.
- **Defenses** (`services/defense.py`): Blue Team spends points to enable
  per-session switches (system prompt patch, rate limiter, reranker, JWT strict
  mode).

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

## Backend module map (`backend/app/`)

| Package | Contents |
|---|---|
| `main.py` | App, mounted `red_app` / `blue_app`, lifespan (secret check, stores, model, SOC task), CORS, body size limit |
| `api/` | Routers: `routes_red.py`, `routes_blue.py`, `routes_ctf.py` |
| `core/` | `config.py` (settings), `security.py` (JWT, passwords, session authorization), `llm_engine.py` (model streaming), `prompt_builder.py`, `queue_manager.py`, `redis_client.py` |
| `llm/` | `provider.py`: model factory (OpenAI-compatible, Groq, local GGUF) |
| `agents/` | `soc_agent.py` (LangGraph SOC analyst), `tools/` (tool registry: schema, risk level, audited handler; `send_email`) |
| `services/` | `rag_service.py`, `etl_worker.py`, `soc_worker.py`, `forensics.py`, `defense.py`, `rate_limiter.py`, `email_service.py`, `siem_logger.py` |
| `db/` | `models.py`, `repository.py`, `database.py`, `chroma_client.py`, `object_store.py`, `sqli_isolation.py`, `sqli_search.py` |
| `schemas/` | Pydantic request/response models |

Dependencies point inward: routers → services → repositories. `core/` must not
import domain repositories. Domain modules (router, service, repository and
schemas per domain) are being introduced step by step (ARC-3).

## Configuration and secrets

- Settings come from `.env` (`core/config.py`); `.env.example` lists every
  variable with placeholders.
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
