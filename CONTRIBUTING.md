# Contributing

## Tasks

Every change starts from a GitHub issue created with the **Task** template.

- ID format: `[AREA]-[N]`, numbered within each area, e.g. `SEC-2`.
- Issue title: `[AREA-N] Short description`, with the matching `area:*` label
  and the phase milestone.
- Each issue states its goal, acceptance criteria, deliverables and blocking
  issues. Do not start a task while it is blocked.

| Area | Scope |
| --- | --- |
| `OPS` | Repository, process, CI, documentation |
| `SEC` | Platform isolation and security (not intended lab vulnerabilities) |
| `ARC` | Modular architecture and refactoring |
| `FE` | Frontend |
| `AGT` | Agent infrastructure, MCP, tools |
| `DET` | Attack detection, classifier, SOC, Blue Team economy |
| `RED` | Attacker agent, datasets, fine-tuning |
| `LAB` | OWASP LLM Top 10 exercises |
| `EVAL` | Defense measurement and reports |

## Branches and pull requests

- One task, one branch, one pull request into `main`.
- Create the branch from an up-to-date `main`: `AREA-N-short-slug`,
  e.g. `SEC-2-jwt-secret-check`.
- Commit messages: `AREA-N: what changed`.
- The pull request description starts with `Closes #<issue>` and lists how the
  change was verified.
- Every pull request has exactly one review level label. It picks the model of
  the automated Azure review:

  | Label | Use for |
  | --- | --- |
  | `review:easy` | Docs, comments, config values, mechanical renames without logic changes |
  | `review:medium` | Ordinary logic changes with tests (the default when no label is set) |
  | `review:hard` | Platform security and isolation, auth and tokens, cross-module refactors, concurrency, data migrations |

  Adding a label at creation fires the review twice (`opened` and `labeled`).
  Open the PR as a draft with the label, then mark it ready, so it runs once:

  ```bash
  gh pr create --draft --label review:medium ...
  gh pr ready
  ```

  Changing the label later starts a new review with the new level.
- After merge the remote branch is deleted automatically. Clean up locally:

  ```bash
  git switch main && git pull --ff-only
  git fetch --prune
  git branch -d AREA-N-short-slug
  ```

## Language

Issues, pull requests, commit messages, code comments and documentation are
written in English.
