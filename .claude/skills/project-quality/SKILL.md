---
name: project-quality
description: Enforce AI Trace's autonomous delivery, verification, and exact-review standard for non-trivial implementation, bug-fix, security, persistence, UI, and release work.
---

# AI Trace Project Quality

Use this workflow for any non-trivial change. Read `AGENTS.md`, `CLAUDE.md`, and the closest scoped instructions before editing.

## Execute

1. Inspect `git status -sb` and preserve unrelated work.
2. State assumptions, scope, and measurable success criteria.
3. Search API routers, models, observability runtime, operations scheduler, security, tracing providers, migrations, production locks, Docker contracts, and runbooks before creating a new concern home.
4. Reproduce bugs or contract gaps with the narrowest useful test.
5. Implement the smallest complete fix and update every touched caller, export, registry, migration, and contract.

## Verify

Run focused unit or integration pytest first, then configured Ruff and Pyright scopes, the security gate, migration checks, and exact Docker image or health probes when runtime behavior changed.

Do not infer success from a status flag, HTTP 200, mock, or healthy container alone. Inspect organization-scoped trace or session data, detector evidence, policy decision, active-leader action, durable operation record, notification delivery, and operator-visible degraded, standby, contending, or error state. Exercise negative authorization and failure or recovery cases whenever those boundaries changed.

## Review and handoff

Material production, security, isolation, autonomous-mutation, persistence, provider-routing, or public-UI changes require the independent exact-commit review in `.github/ai-review/senior-review.md`. HIGH or MEDIUM findings block merge or deployment.

Report exact branch and head, tests and probes run, runtime or public evidence, review status, rollback and cleanup state, and anything still unverified. Never fabricate evidence.
