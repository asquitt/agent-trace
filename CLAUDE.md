# AI Trace - Agent Observability & Runtime Governance

## Autonomous Delivery Standard

These rules govern implementation, diagnosis, review, release, and handoff. More specific project invariants below remain binding.

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
- The normal AI Trace path is focused pytest, configured Ruff and Pyright scopes, the security gate, migration checks, and exact Docker image or health probes for runtime changes.
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

### Handoff

Report the exact branch and head, files changed, tests and probes run, runtime or public evidence, independent-review result, rollback and cleanup state, and remaining risks. Distinguish complete, partial, blocked, and unverified work explicitly.

### AI Trace risk focus

Prioritize tenant isolation, leader versus standby truth, advisory locking, stale-session projections, monotonic activity watermarks, approval-gated shutdown, dependency locks, production startup, and the development-only dashboard boundary.

## Quick Start
```bash
cd docker && docker compose up -d db redis
cd .. && pip install -e ".[dev]"
alembic upgrade head
uvicorn src.api.main:app --reload
```

## Architecture

**Agent Fleet Observability**: Real-time monitoring, anomaly detection, policy enforcement, and governance audit for production AI agent fleets.

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

## Commands
```bash
uvicorn src.api.main:app --reload       # Dev server
pytest -q -p pytest_cov -p pytest_asyncio  # Run tests
ruff check --select F src tests         # Lint
pyright                                 # Type check
alembic upgrade head                    # Run migrations
./scripts/run_full_e2e.sh               # Full E2E validation
./scripts/security_gate.sh              # Security scan (bandit + pip-audit)
```

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
- `alert` → notify via webhook/Slack/PagerDuty
- `throttle` → rate-limit session
- `approve` → require human approval
- `shutdown` → terminate agent session (approval-gated)

### Observability Runtime
- Background scheduler loop checks anomalies, evaluates policies, executes actions
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
4. **Migrations**: `alembic upgrade head` succeeds

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

## Debug
```bash
curl -s http://localhost:8000/health/live | jq
curl -s http://localhost:8000/api/v1/observability/dashboard/ui  # Dashboard
docker compose logs api --tail=50
```

## Health
- API: localhost:8000
- PostgreSQL: localhost:5434 (pgvector)
- Redis: localhost:6379
