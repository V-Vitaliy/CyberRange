# Review instructions

CyberRange is a vulnerable-by-design lab. Some vulnerabilities are features. Review
against `docs/architecture.md` and `AGENTS.md`; the issue linked in the PR
(`Closes #N`) defines the scope and acceptance criteria.

## Intentional lab vulnerabilities: do not report them as bugs

These are exercises. Report them only if a change lets them **escape the
attacker's own lab** or removes them unintentionally.

- **Prompt injection** in chat and in indexed documents; the model following
  instructions found in user input or retrieved context.
- **SQL injection** in `GET /api/red/chat/history` (`chat/sqli_search.py`): the
  query string is concatenated on purpose and the SQLSTATE error text is
  returned on purpose. It must run only as the lab's read-only role against the
  lab's `security_barrier` views.
- **JWT role forgery** in `get_current_user_vulnerable`: the `role` claim is read
  without signature verification on purpose. Identity and lab must still come
  from the signed `lab_scope` claim.
- **Path traversal** in `POST /api/red/upload`: `../student_uploads/` reaching
  the indexed folder is intended (`resolve_upload_target`). Keys must stay under
  the caller's `<lab_id>/` prefix.
- **Tool abuse**: the agent may be talked into calling `send_email`, including
  with secrets. Delivery must stay limited to `EMAIL_ALLOWED_DOMAINS`.
- **Weak defaults inside the game**: features that are off until the Blue Team
  buys a defense (rate limiter, reranker, prompt patch, JWT strict mode).
- **Login timing difference** between unknown and known usernames (planned
  exercise EX-07).

## Review priorities

1. **Lab isolation (critical).** Any path where a student reaches another lab's
   data, the control-plane database, Redis, Chroma, S3, Mailpit, the host or the
   internet. Check that the lab ID comes from server-side state or a signed
   claim, never from request input; every Chroma query filters by
   `lab_instance_id`; session access goes through `labs.service.get_authorized_session`; new
   object keys and SIEM events are lab-scoped.
2. **Platform secrets and configuration.** No secrets, tokens, passwords or flags
   in code, tests, docs or logs. No usable defaults for credentials; services
   must refuse to start without them. New Compose ports bind to
   `${STATE_BIND_ADDRESS}` / `${COMPUTE_BIND_ADDRESS}` (loopback by default) and
   require authentication.
3. **Internal details reaching clients.** Exceptions must not be returned to
   clients as `str(e)` (except the intended SQLi error). Use generic messages and
   `logger.exception` on the server.
4. **Event loop blocking.** No blocking or CPU-heavy calls on the asyncio loop:
   bcrypt, embeddings, reranker, PDF parsing, synchronous HTTP/S3/Chroma
   clients, SMTP, `time.sleep`. Use `asyncio.to_thread`, or the ETL executor for
   document processing. Do not hold a database transaction or row lock while
   awaiting the model.
5. **Correctness.** Error paths, retries, idempotency, races between concurrent
   requests, resource cleanup (engines, tasks, files), and behavior when Redis,
   Chroma, S3 or the model are unavailable.
6. **Tests.** Behavior changes come with tests in the module's existing test
   file (`tests/unit/test_<module>.py`, `tests/api/test_<router>.py`). Flag
   weakened or removed assertions, tests that cannot fail, mocks presented as
   verification of real services, and integration tests that may silently skip
   in CI.
7. **Scope and simplicity.** Changes outside the issue's scope, speculative
   abstractions, dead code, and new dependencies without need. Prefer the
   simplest design that meets the acceptance criteria (KISS).

## Conventions to check

- Everything in the repository is English: code, comments, docs, commit
  messages. Comments only where intent is not obvious.
- PR title `[AREA-N] Title`, body starts with `Closes #N`; commits `AREA-N: ...`.
- Dependencies point inward: routers → services → repositories; `core/` does not
  import domain repositories.
- Public docs (`docs/`, `README.md`) must not contain exercise solutions or
  attack paths; the repository is public.
- Logging uses module loggers, never `print` (enforced by ruff `T20`).

## Severity guide

- **critical**: lab isolation escape; secret exposure; remote code execution or
  file access on the host.
- **high**: authentication or authorization bypass outside the intended
  exercises; internal details leaked to clients; event loop blocking on a
  request path; data loss.
- **medium**: incorrect behavior on error paths; missing tests for changed
  behavior; races without security impact.
- **low**: style, naming, documentation gaps.

Do not report formatting issues that ruff enforces, or the intended
vulnerabilities above when they stay inside the lab.
