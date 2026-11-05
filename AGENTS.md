# CyberRange: instructions for contributors and coding agents

CyberRange is an educational, vulnerable-by-design lab for attacks on and
defenses of LLM applications. Read `README.md` for the product overview and
`LOCAL_RUN.md` for setup. Verify claims against the current code; documentation
may lag.

## Principles
- **KISS.** Solve the current task with the simplest working design. No
  speculative abstractions, options or layers "for the future".
- **Modular architecture.** A domain module owns its router, service,
  repository and schemas. Dependencies point inward: routers call services,
  services call repositories; `core/` must not import domain repositories.
  New code goes into the module it belongs to, not into shared catch-alls.
- **Clean code.** Small functions with clear names, explicit types, no dead
  code or commented-out code. Match the style of the surrounding code.
- **Comments** only where intent is not obvious from the code.
- **English** for everything in the repository and on GitHub: code, comments,
  docstrings, docs, commit messages, issues and pull requests.

## Lab vulnerabilities vs. platform security
Intentional vulnerabilities (prompt injection, SQLi, JWT role forgery, path
traversal, tool abuse) are features. They must stay inside the attacker's own
lab: a student must never reach another lab's data, the control-plane database,
infrastructure services or the outside world. Keep these boundaries enforced by
the server. If a requirement conflicts with isolation, stop and ask instead of
relaxing isolation.

## Tasks and branches
Follow `CONTRIBUTING.md`:
- Work only on a GitHub issue `[AREA-N] ...` that is not blocked.
- One task, one branch `AREA-N-short-slug` from up-to-date `main`, one PR
  starting with `Closes #<issue>`. Commit messages: `AREA-N: what changed`.
- Stay within the issue's scope; report out-of-scope problems instead of fixing
  them silently.
- After merge, delete the local branch.

## Project map
- `backend/app/`: FastAPI application.
  - Domain packages `app/<domain>/` (`router.py`, `service.py`, `repository.py`,
    `schemas.py` as needed). Migrated so far: `labs/` (game sessions), `auth/`
    (users, passwords, JWT, login routes), `ctf/` (flag submission). Other
    domains use a domain's `service`, never its `repository`;
    `tests/unit/test_architecture.py` enforces this.
  - Not yet migrated (ARC-3): `api/` routes, `services/` business logic,
    `db/repository.py`. Shared: `core/` configuration and LLM engine,
    `db/` models and database, `llm/` model factory, `agents/` LangGraph agents
    and tools, `schemas/` request/response models.
- `backend/tests/`: backend tests (see below).
- `frontend/src/`: Next.js UI; `frontend/tests/`: Node tests.
- `scripts/`: database setup and SQLi provisioning CLIs.
- `docs/`: public project docs (`exercises.md` is the student-facing exercise catalog).
  `docs/local/` is gitignored: working notes, plans and exercise solutions. Never put
  attack paths or solutions in tracked docs; the repository is public.

## Tests
- Every behavior change comes with tests. Never weaken assertions, add
  unconditional success, or present mocked tests as verification of real
  PostgreSQL, Redis, Chroma or model services.
- Add tests to the existing test file of the module under test. Do not create
  a new test file per task. Shared fixtures live in `conftest.py`.
- Layout: `tests/unit/test_<module>.py`, `tests/api/test_<router>.py`,
  `tests/integration/` for opt-in tests against real services.

Commands from the repository root:
- Backend: `PYTHONPATH=backend python -m pytest -q -ra backend/tests`
- Single group: `PYTHONPATH=backend python -m pytest -q -ra backend/tests/<group>`
- Frontend: `node --test frontend/tests/*.test.mjs` (Node 24)
- Types: `cd frontend && npx tsc --noEmit`
- Lint and format: `ruff check .` and `ruff format .` (CI runs `ruff format --check .`)

## Workspace and data
- Preserve existing user changes; never reset, discard or force-push others' work.
- Never commit secrets, `.env`, uploaded documents, flags or credentials.
- Use explicitly designated test databases and services; never reset shared
  data.
- Local, machine-specific instructions (delegation tooling, local services)
  belong in the gitignored `AGENTS.local.md`.
