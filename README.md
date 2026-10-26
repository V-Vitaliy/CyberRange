# AI Security CyberRange

An educational, vulnerable-by-design lab for exploring attacks and defenses in
LLM applications. Red Team exercises use the API; Blue Team has a web dashboard
for inspecting audit events, investigating incidents and purchasing defenses.
Intentional SQL injection, JWT role manipulation and document-upload behavior
are part of the exercises and must remain within the assigned laboratory.

## Current state — October 2026

**The core local workflow is operational with an OpenAI-compatible Qwen server.**
The project has completed a focused stabilization pass, but is still a learning
prototype with known limitations. It is not a finished product or a production
security boundary suitable for public exposure.

| Area | Implemented behavior | Verification / limits |
| --- | --- | --- |
| Authentication and lab access | Red/Blue login, lab-scoped sessions, threads, logs and documents; Swagger Bearer authentication | Real local API checks and regression tests. Intended Red JWT role forgery stays inside the authenticated lab. |
| PDF RAG | Text extraction, chunking, Chroma retrieval, streamed answers and PostgreSQL chat history | A user-supplied Polish PDF produced a correct answer. One unsupported question produced an explicit lack-of-information answer; this is not a general hallucination guarantee. |
| Model connections | Configurable OpenAI-compatible HTTP server, existing Groq integration and in-process GGUF loading | Local Qwen streaming, email tool calls and SOC output were checked. Real Groq and GGUF execution were not verified in this stabilization pass. |
| Educational SQLi | Vulnerable history search through per-lab restricted PostgreSQL readers and security-barrier views | Normal search, intentional injection, denied control-plane access and error recovery checked against test PostgreSQL. Requires explicit SQLi provisioning. |
| Blue Team | Dashboard, SIEM events, manual investigations, defense budget and purchases | API state, sequential repeat rejection and restart persistence checked. Full browser interaction coverage is not claimed. |
| Switchable defenses | JWT signature enforcement, Redis rate limiting, system-prompt patch and optional reranker | JWT/rate-limit behavior checked before and after purchase. Prompt patch state persists;  |
| Auto-SOC | Background LLM analysis for solo sessions, with strict structured-output validation | Successful and failed analysis checked. Invalid or failed analysis leaves events pending rather than marking them safe. Verdict accuracy is not guaranteed. |
| Email tool | Model tool-call handling, audit events and simulated or configured SMTP delivery | Simulation checked with SMTP disabled. No real email was sent during verification. |
| CTF | SHA-256 flag lookup scoped to the authenticated lab, submissions and sequential duplicate rejection | Capture confirmed locally and regression-tested. Concurrent submissions can still create duplicates. |
| Database setup | Explicit, repeatable creation of tables, users and a session | Checked against a designated test PostgreSQL database. Existing passwords/game state are preserved; this is not a schema migration system. |

## Running locally

See [LOCAL_RUN.md](LOCAL_RUN.md) for dependencies, environment variables, service
startup, database setup, SQLi provisioning and API usage. The current target is
a local installation using the existing Compose services, not a completed
two-server deployment.

| Component | Default local address |
| --- | --- |
| PostgreSQL | `localhost:5432` |
| Redis | `localhost:6379` |
| Chroma | `localhost:8000` |
| Backend | `http://127.0.0.1:8001` |
| Red Team Swagger | `http://127.0.0.1:8001/api/red/docs` |
| Blue Team Swagger | `http://127.0.0.1:8001/api/blue/docs` |
| Blue Team dashboard | `http://localhost:3000/blue-team/dashboard` |

For the local OpenAI-compatible server, an example configuration is:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://127.0.0.1:8080/v1
LLM_MODEL=qwen
LLM_API_KEY=
LLM_MAX_TOKENS=2048
SMTP_HOST=
```

Use your server's actual model ID and key if authentication is required. It must
support streaming chat completions and the tool calls used by chat and SOC;
an OpenAI-compatible endpoint alone does not guarantee every capability.
Without `LLM_PROVIDER`, a nonempty `GROQ_API_KEY` selects Groq; otherwise the
existing GGUF loader is used. An HTTP server does not require a local GGUF file.

Documents and flags are supplied by the operator. Setup does not download PDFs,
index a dataset or register flags. PDF upload and CTF registration are separate:
CTF needs the flag's SHA-256 hash, the owning lab UUID and a reward in PostgreSQL.
The file in `scripts/golden_data/` is a placeholder, not a usable dataset.

There is no Red Team browser chat yet. Use Swagger, an HTTP client or the
separately delivered `.local-tools/rag_probe.py` helper. That helper, uploaded
documents, credentials and local verification notes are ignored by Git and are
not included in a fresh clone.

## How the existing application works

- **FastAPI + PostgreSQL:** authentication, sessions, chat history, SIEM logs,
  defense state and CTF records.
- **Redis:** request counters and ETL job status. The current rate limiter uses
  a per-session/IP counter with a 60-second expiry.
- **PDF pipeline:** `pypdf` extracts text; NLTK splits sentences; `tiktoken`
  estimates chunk sizes with overlap. These are approximate token targets,
  since whole sentences are retained. Scanned PDFs need external OCR.
- **Retrieval:** CPU `all-mpnet-base-v2` embeddings and Chroma, with lab/access
  metadata filters. The optional cross-encoder reranker is implemented but has
  the memory limitation noted above.
- **Inference:** streamed chat and a shared model queue; Auto-SOC uses a LangGraph
  workflow with the selected model. Manual investigation scoring currently uses
  regex heuristics, not a trained ML classifier.
- **Storage:** uploaded PDFs are local files. MinIO remains in the compute
  Compose file but is not used by the current PDF upload path.

## Tests and confidence

The latest local review ran **133 backend tests successfully**, with **2 real
PostgreSQL integration tests skipped** because their explicit opt-in variables
were unset. This clean-environment run used Python 3.14; GitHub Actions is
configured for Python 3.11. Earlier, separate PostgreSQL and local-model checks
were run against designated test services. These are different kinds of evidence.

```bash
python -m pip install -r backend/requirements-test.txt
PYTHONPATH=backend python -m pytest -q -ra backend/tests
node --test frontend/tests/*.test.mjs
```

See [backend/tests/README.md](backend/tests/README.md) for the separately enabled
PostgreSQL checks. The default suite uses mocks/HTTP doubles and an in-memory
SQLite CTF fixture; passing CI does not establish a working live model or full
PostgreSQL/Redis/Chroma integration. Frontend tests, TypeScript and a production
build have also passed locally during stabilization.

[GitHub Actions](.github/workflows/test.yml) runs backend tests, frontend tests,
TypeScript checks and a Next.js build on pushes and pull requests. It does not
start the full application stack or call a real model.

## Remaining work and future direction

The next priorities are reliability and clearer verification of existing features:

1. Make CTF submission deduplication atomic, including concurrent requests.
2. Address reranker device/memory configuration and verify the enabled defense
   while the inference server is running.
3. Extend reproducible integration coverage: adversarial cross-lab document
   retrieval, concurrent state changes, real Groq/GGUF execution and browser flows.
4. Evaluate manual regex-based scoring and SOC verdict quality with controlled
   examples. Current heuristics and LLM judgments can misclassify events.
5. Measure system-prompt and reranker defense behavior instead of treating an
   enabled switch as proof of attack resistance.

The following are unfinished ideas, not completed features or scheduled releases:

- A Red Team chat/upload UI, browser ETL progress and JWT editor.
- Auto-grading, metrics and CSV reports: `scripts/admin_report.py` is a stub.
- Lab teardown/cleanup: `scripts/admin_teardown.py` is a stub. No automated
  deletion/reset workflow is provided.
- A reproducible server deployment guide and capacity measurements.

PDF selection and flag placement remain operator-managed. No generated Golden
Dataset, attacking agent, ML training pipeline or new retrieval system is part
of the completed stabilization work.
