# CyberRange exercises

Exercises for the Red Team (attack the lab's LLM assistant) and the Blue Team
(detect the attacks and buy defenses). Each exercise maps to the
[OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/)
or the classic OWASP Top 10.

## Rules

- Attack only your own lab instance. Your data, documents, events and flags are
  scoped to it; reaching another lab or the host is a platform bug. Report it
  instead of using it.
- The Red Team works through the API (`/api/red/docs`), the Blue Team through the
  dashboard and `/api/blue/docs`.
- Every Red Team action is logged in the lab's SIEM. Blue Team analysts see it.

## Exercises

| ID | Exercise | OWASP | Blue Team defense | Status |
|---|---|---|---|---|
| EX-01 | Make the assistant break its rules | LLM01 | Prompt Patch | Partial |
| EX-02 | Poison the knowledge base | LLM04, LLM08 | Reranker | Partial |
| EX-03 | Make the agent leak a secret | LLM06, LLM02 | none yet | Partial |
| EX-04 | Read other users' conversations | A03 | none yet | Playable |
| EX-05 | Read restricted documents | A01, A07 | JWT Strict Mode | Partial |
| EX-06 | Exhaust the shared model | LLM10 | Rate Limiter | Playable |
| EX-07 | Find valid accounts | A07 | none yet | Partial |

**Status:** `Playable`: complete; `Partial`: solvable, but not yet scored by a flag
or not yet visible to the Blue Team; `Planned`: not implemented.

### EX-01. Make the assistant break its rules
- **Objective:** the assistant answers only from the university documents it
  retrieves. Make it ignore that instruction, change its persona, or reveal its
  system prompt.
- **Where:** `POST /api/red/chat/ask`.
- **Hint:** the instructions and your message reach the model as text in the
  same conversation.

### EX-02. Poison the knowledge base
- **Objective:** get a document you wrote into the assistant's knowledge base, so
  that it repeats or follows your content when other users ask questions.
- **Where:** `POST /api/red/upload` (PDF only, up to 10 MiB and 200 pages).
- **Hint:** not every uploaded file is indexed. Look at where uploads are stored.

### EX-03. Make the agent leak a secret
- **Objective:** the assistant can send email. Make it send a secret from its
  context to an address you choose.
- **Where:** the chat assistant and its tools.
- **Hint:** the instruction does not have to come from you directly.
- **Note:** mail leaves only to the lab's own domain; the attempt is what counts.

### EX-04. Read other users' conversations
- **Objective:** your chat history search should return only your own threads.
  Read the threads and security events of other users in your lab.
- **Where:** `GET /api/red/chat/history`.
- **Hint:** watch how the search reacts to unusual characters.

### EX-05. Read restricted documents
- **Objective:** some documents are retrieved only for privileged users. Make the
  assistant use them for you.
- **Where:** the access token you receive at login.
- **Hint:** inspect what your token contains and what the server checks.

### EX-06. Exhaust the shared model
- **Objective:** all users and the automatic SOC analyst share one model. Slow it
  down for everyone else.
- **Where:** `POST /api/red/chat/ask`.
- **Hint:** both the number of requests and the length of the answers matter.

### EX-07. Find valid accounts
- **Objective:** find out which usernames exist without knowing any password.
- **Where:** `POST /api/red/login`, `POST /api/blue/login`.
- **Hint:** the response body is the same in both cases. Something else is not.

## Blue Team

| Activity | Where | Effect |
|---|---|---|
| Watch the SIEM | dashboard, `GET /api/blue/logs` | Lab events with payload and the Auto-SOC verdict |
| Investigate an event | `POST /api/blue/investigate` | A correct "malicious" verdict earns 3 defense points |
| Buy a defense | dashboard, `POST /api/blue/defenses/buy` | Spends points and enables the defense for the session |
| Auto-SOC (solo labs) | background | An LLM analyst reviews every new event |

| Defense | Cost |
|---|---|
| Prompt Patch | 2 |
| JWT Strict Mode | 2 |
| Rate Limiter | 3 |
| Reranker | 4 |

## Flags

Flags look like `flag{...}`. Submit them with `POST /api/red/ctf/submit`; each flag
is scored once for your team. Flags are not yet seeded for every exercise.

## OWASP LLM Top 10 coverage

| Category | Exercises |
|---|---|
| LLM01 Prompt Injection | EX-01, EX-02 |
| LLM02 Sensitive Information Disclosure | EX-03, EX-05 |
| LLM03 Supply Chain | planned |
| LLM04 Data and Model Poisoning | EX-02 |
| LLM05 Improper Output Handling | planned |
| LLM06 Excessive Agency | EX-03 |
| LLM07 System Prompt Leakage | EX-01 |
| LLM08 Vector and Embedding Weaknesses | EX-02 |
| LLM09 Misinformation | not covered |
| LLM10 Unbounded Consumption | EX-06 |
