# AI Trace - Internal Telemetry and Incident-Evidence Inventory

## Autonomous Delivery Standard

These rules govern implementation, diagnosis, review, release, and handoff. More specific project invariants below remain binding.

## Binding Product Disposition

- `docs/DISPOSITION.md` governs this repository. AI Trace is a private internal implementation inventory, not a standalone product, hosted service, public package, or generic control plane.
- The default activity is read-only source and test evaluation. Repository maintenance is limited to governance, dependency/security, or risk-reducing corrections that add no capability, compatibility promise, deployment path, or product-adoption behavior.
- Every capability expansion or adoption change must identify a named active product, accountable owner, product-local acceptance criteria, and operator or customer outcome. Missing ownership or evidence is a `HOLD`, not permission to generalize the repository.
- The first consumer implements the needed behavior in that product's canonical layer and owns authentication, tenancy, data minimization, persistence, operations, rollback, and evidence.
- Shared extraction is prohibited until two independent active products satisfy every item in the second-consumer gate in `docs/DISPOSITION.md`. Similar code or one consumer is insufficient.
- Do not publish or deploy this repository, enable provider or outbound side effects, or run migrations against a product or shared database without a new explicit owner decision that satisfies the disposition.

### Scope and planning

- State assumptions, scope, and measurable success criteria before multi-step work.
- Turn the plan into `step -> verification` pairs and keep it current.
- Proceed autonomously with routine, reversible work inside the requested scope.
- Stop for missing credentials, destructive or production-wide actions, ownership conflicts, or choices that materially change the product.
- A request to diagnose, review, or report is read-only unless the user also authorizes changes.
- Inspect `git status -sb` first. Preserve unrelated tracked, untracked, staged, and generated work.

### Simplicity and adoption

- Write the minimum code that completely solves the requested problem.
- Search callers, registries, shared services, adapters, hooks, and existing implementations before adding a new abstraction or concern home.
- Extend the canonical layer when responsibility is the same. If a shared layer changes, migrate the touched callers rather than creating a sibling implementation.
- Do not add speculative configuration, redundant fallback paths, or infrastructure for remote hypotheticals.
- Update affected imports, exports, routers, task maps, migrations, schemas, clients, and documentation.
- Remove only dead code made obsolete by the current change. Do not clean unrelated debt.

### Outcome truth and no false greens

- A passing test, HTTP 200, status flag, database row, mock, screenshot, or healthy container is evidence, not proof of the claimed outcome.
- Verify the real producer, persistence, ownership boundary, consumer, and user or operator-visible result.
- For AI Trace, trace organization-scoped sessions and evidence through detection, policy, active-leader execution, durable operation records, notification delivery, and truthful operator states; only the active scheduler leader is healthy.
- Mocks and deterministic fixtures are acceptable development evidence only. Label them clearly and do not use them to support live-provider or production claims.
- Deployment proof requires exact repository, revision, artifact or image, configuration contract, health behavior, and rendered application identity.
- Never weaken a customer-facing claim to make QA pass. Fix the behavior or request an explicit positioning decision.

### Verification

- Bug fix: reproduce with a focused regression, implement the smallest fix, and prove the regression now passes.
- Start with the narrowest relevant checks, then run changed-file gates and broader build, integration, E2E, security, or release gates in proportion to risk.
- The normal AI Trace path is focused pytest plus configured Ruff and Pyright scopes. Security, migration, Docker, API, or health checks are proportional follow-ups only for explicitly authorized risk-reducing maintenance or a product-owned change, and must use an isolated target. Verification does not authorize deployment, provider execution, adoption, or capability expansion.
- Test negative and adversarial cases for auth, isolation, validation, retries, partial failure, rollback, and cleanup when those boundaries change.
- Verify APIs with actual requests and response bodies, UI with a real render and interaction, persistence with stored and reloaded state, and background work with produced results and logs.
- If a required gate cannot run, report exactly what passed, what failed, and what remains unverified.

### Independent senior review

- Material production behavior, security boundaries, autonomous mutations, persistence, provider routing, migrations, deployment controls, and public UI changes require one separate independent xhigh review of an immutable base-to-head commit.
- Give the reviewer the full base and head SHAs, user requirements, affected architecture, tests, and runtime context. The reviewer is read-only and follows `.github/ai-review/senior-review.md`.
- HIGH or MEDIUM findings require a plausible current trigger, concrete impact, reproducible evidence, an affected file and line, and the smallest sufficient fix.
- Freeze the candidate on any admitted blocker. Fix narrowly, rerun relevant gates, commit a new head, and request one bounded re-review from the same reviewer.
- LOW, speculative, unrelated, stylistic, and non-reproducible observations do not extend the cycle.
- CI, previews, and deployment checks are separate evidence and do not replace independent review.

### Git, secrets, and production safety

- Stage and commit only files belonging to the current task. Use conventional, coherent commits without AI co-author trailers.
- Push once at the requested or final handoff boundary. Never force-push, rewrite history, or change remotes unless explicitly requested.
- Never print, log, screenshot, commit, or place secrets in command arguments. Source only required variables from an approved local or external secret store.
- Production starts read-only: verify target, identity, health, logs, rollback, and cleanup path before mutation.
- Never run destructive database, provider, deployment, or filesystem operations against an unresolved or broad target.
- Do not claim deployment, rollback, cleanup, or public verification that was not directly observed.

### Commit cadence and pull-request lifecycle

- One branch and one pull request represent one coherent customer-impact or operational slice. Start material work from the latest verified default branch on `codex/<short-slug>` unless the task already owns a suitable branch.
- Commit each verified, bisectable checkpoint and at least once at the end of a successful work session. Do not wait for a large dump, create noisy save-point commits, or mix unrelated cleanup.
- Every commit must preserve focused green evidence for its changed property. Use conventional messages, stage only task-owned files, and never add AI co-author trailers.
- Batch local commits and push once at the authorized session or correction-cycle boundary. Do not push after every commit, force-push, amend, rebase, or otherwise rewrite a candidate that has been shared or reviewed.
- At the first authorized push for a material slice, open or update one draft pull request; never create a duplicate PR for the same branch. Documentation that accompanies code stays in that PR. Do not create a PR solely for low-risk documentation unless repository protection requires it.
- Keep the PR draft while required tests, preview or runtime proof, rollback planning, cleanup, or independent exact-commit review remains incomplete. Record exact base, head, and tree SHAs plus the commands and results that support the candidate.
- Freeze the PR head for independent review. HIGH or MEDIUM findings keep it blocked and draft; make the smallest coherent fix as a new commit, push once, freeze the new head, rerun affected gates, and request bounded re-review.
- Mark ready and merge only when the current remote head is the reviewed head, required evidence is green, conversations are resolved, and the task includes merge or release authority. Use a merge commit, not squash or rebase, so candidate commits and review identities remain recoverable.
- An accepted PR is merged and GitHub closes it automatically. Manually close only an abandoned, duplicate, or explicitly superseded PR, and leave a final comment naming the reason, preserved head SHA, remaining blockers, and successor when one exists. Never close a PR to hide a blocker.
- After merge, record the PR number and merge SHA, verify the merged tree and any required deployment or public behavior, then report rollback and cleanup state. Delete a branch or worktree only when it is merged or superseded, clean, idle, and explicitly released; otherwise preserve it.

### Handoff

Report the exact branch and head, files changed, tests and probes run, runtime or public evidence, independent-review result, rollback and cleanup state, and remaining risks. Distinguish complete, partial, blocked, and unverified work explicitly.

### AI Trace risk focus

For an explicitly authorized component evaluation, prioritize tenant isolation, leader versus standby truth, advisory locking, stale-session projections, monotonic activity watermarks, approval-gated shutdown, dependency locks, and the development-only dashboard boundary. Do not treat those historical runtime surfaces as an active production roadmap.

## Read-Only Evaluation Start
```bash
python -m pytest <target> -q
ruff check --select F src tests
pyright
```

Do not start Docker, run migrations, launch the API, call providers, send notifications, or deploy by default. A required runtime check must belong to explicitly authorized risk-reducing maintenance or a product-owned change and use an isolated disposable target. The check itself does not authorize deployment, provider execution, adoption, or capability expansion.

## Historical Inventory

The repository preserves telemetry, anomaly, policy, durable-control, and audit implementations for bounded evaluation. Their presence does not establish a supported control plane, production fleet integration, or active runtime.

```
src/
├── api/
│   ├── main.py              # FastAPI app, middleware, lifespan
│   └── routers/
│       ├── traces.py         # Trace inspection APIs
│       └── observability.py  # Fleet, sessions, policies, runtime
├── cli/
│   ├── trace_viewer.py       # CLI for trace inspection
│   └── production_preflight.py # Deployment readiness checks
├── models/                   # SQLAlchemy ORM models
│   ├── base.py              # Base model with timestamps
│   ├── trace.py             # Trace, span, reasoning models
│   ├── observability.py     # Sessions, policies, deployments, anomalies
│   ├── idea.py              # Idea ranking models
│   └── ranking.py           # Ranking logic
├── services/                # Business logic
│   ├── observability_runtime.py  # Detector + policy evaluation/actions
│   ├── operations_scheduler.py   # Background scheduler loop
│   ├── notifications.py          # Webhook/Slack/PagerDuty dispatch
│   └── ranking.py                # Idea ranking service
├── tracing/                 # Tracer implementation
│   ├── tracer.py            # Core Tracer with context managers
│   ├── context.py           # TraceContext + async propagation
│   ├── types.py             # TraceData, SpanData types
│   ├── decorators.py        # @trace decorator
│   ├── storage/postgres.py  # PostgreSQL trace backend
│   └── providers/           # Anthropic/OpenAI wrappers
├── config.py                # Settings (150+ fields)
├── database.py              # SQLAlchemy async engine
├── security.py              # API auth, RBAC, org scope
└── rate_limit.py            # Request rate limiter

tests/
├── unit/                    # Unit and contract tests
└── integration/             # API and persistence integration tests

alembic/versions/            # 7 migration files
```

## Tech Stack
- **Language**: Python 3.11+
- **Web**: FastAPI + Uvicorn
- **Database**: PostgreSQL 16 + pgvector
- **ORM**: SQLAlchemy 2.0+ (async)
- **Migrations**: Alembic
- **Task Queue**: Celery + Redis
- **Type Checking**: Pyright (strict mode)
- **Linting**: Ruff

## Repository Evaluation Commands
```bash
pytest -q -p pytest_cov -p pytest_asyncio  # Run tests
ruff check --select F src tests         # Lint
pyright                                 # Type check
./scripts/security_gate.sh              # Security scan (bandit + pip-audit)
```

`./scripts/run_full_e2e.sh`, migrations, Docker, API startup, provider calls, outbound delivery, and deployment are conditional evidence tools, not a quick start. Use them only when explicitly authorized risk-reducing maintenance or a product-owned change requires that exact isolated proof. Verification does not authorize deployment, provider execution, adoption, or capability expansion.

## File Placement Rules (MANDATORY)

| File Type | Location |
|-----------|----------|
| API router | `src/api/routers/` |
| ORM model | `src/models/` |
| Service/business logic | `src/services/` |
| Tracer implementation | `src/tracing/` |
| LLM provider wrapper | `src/tracing/providers/` |
| CLI command | `src/cli/` |
| Migration | `alembic/versions/` |
| Unit test | `tests/unit/` |
| Integration test | `tests/integration/` |

## Key Patterns

### Policy Actions
- `alert` → persist alert/control evidence; manual runtime endpoints may dispatch via
  webhook/Slack/PagerDuty, while scheduler dispatch is fail-closed pending a durable outbox
- `throttle` → persist a control request for a future runtime adapter
- `approve` → require human approval
- `shutdown` → persist an approval-gated control request for a future runtime adapter

### Observability Runtime
- Background scheduler loop checks anomalies, evaluates policies, and persists auditable control requests
- No runtime actuator is currently wired; throttle and shutdown requests remain pending until an adapter confirms execution
- Persistent operation run logs for audit

### Auth & Tenant Isolation
- API key + RBAC authorization
- All queries MUST filter by `org_id` — no cross-tenant data leaks

## Dead Code & Orphan Prevention (MANDATORY)

When your changes make files, imports, or functions unused, **delete them in the same commit**.

## NEVER Mark Tasks Complete Without Verification (CRITICAL)

**NEVER claim something is working without ACTUALLY verifying it.**

1. **API responses**: curl the endpoint, check the response
2. **Type check**: `pyright` passes
3. **Tests**: pytest passes
4. **Conditional runtime evidence**: migrations, API probes, or external-boundary checks pass only when the authorized change actually requires them and the target is isolated

**If you cannot verify, say so explicitly. Never fabricate verification results.**

## Workflow Conventions (MANDATORY)

### Immediate Execution, Not Summarization
When implementing from a plan, start execution immediately.

### Scope Discipline
Implement exactly what's requested before expanding.

### Debugging Structure
Start from the error traceback → trace the call chain → identify root cause → then fix.

### API Contract-First Development
Before implementing features:
1. Define Pydantic response model
2. Implement endpoint against schema
3. Verify response matches schema

## Mandatory Architectural Review (BEFORE IMPLEMENTATION)

When modifying 3+ files:
1. **File Size**: Will any file exceed 800 lines?
2. **Tenant Isolation**: Does every query filter by `org_id`?
3. **Existing Patterns**: Does this follow codebase conventions?
4. **Migration**: Does this need a new Alembic migration?

## Decision Authority

**DO autonomously:** Make scoped fixes, run focused tests and quality gates, add task-relevant diagnostics, and remove dead code created by the current change

**ASK first:** Architecture changes, new features, schema migrations, new dependencies, security changes

## Runtime Inventory Boundary

Historical local API, PostgreSQL, Redis, Docker, and console surfaces are not expected to be running. Do not start or inspect them unless explicitly authorized risk-reducing maintenance or a product-owned change requires that isolated evidence.
