# AI Trace

Agent observability and runtime-governance backend for AI-agent fleets.

AI Trace extends trace capture toward an operational control plane: monitor multi-agent
behavior, detect anomalies, evaluate budget and safety policies, persist control
requests, and maintain governance audit trails.

## Why AI Trace

Most LLM observability tools stop at telemetry. AI Trace adds runtime-governance primitives:

- Fleet/session visibility across deployments
- Real-time anomaly detection with deduplication and grouped triage
- Bulk grouped anomaly triage actions (acknowledge/resolve by fingerprint)
- Budget policy evaluation with persisted control requests (`alert`, `throttle`,
  `require_approval`, `shutdown`)
- Memory consistency monitoring across distributed sessions
- Multi-agent delegation chain tracing
- Continuous operations loop (manual + scheduler) with persistent run logs
- Audit and SIEM export paths for governance workflows

## Current Product Status

- Version: `0.2.0`
- Maturity: `Backend Beta / Operator Console Alpha`
- Release posture: not production-ready
- Last code validation: August 27, 2026 (UTC)
- Current validation snapshot:
  - `ruff check --select F src tests` passed
  - `pyright` passed (`0 errors`)
  - `pytest -q` passed (`136 passed`) against PostgreSQL, including browser-session
    persistence, tenant-scoped operator APIs, durable scheduler fencing, and fail-closed
    notification persistence
  - operator console `npm test`, TypeScript validation, production build, and npm audit
    passed (`5 tests`, zero known vulnerabilities)
  - the fail-closed security gate passed against 69 exact hash-locked runtime and build
    dependencies with zero known vulnerabilities
  - the exact-image performance gate passed 284 requests with zero failures after
    matching the 24-request gate concurrency to a 24-connection database pool; write
    p95 was 505ms and the worst read p95 was 597ms
- Release blockers:
  - the authenticated operator console is implemented and locally runtime-verified, but
    no hosted deployment or public-environment identity has been verified
  - runtime `shutdown`/`throttle` delivery and agent acknowledgement are not implemented;
    current controls are persisted requests and audit records only
  - automated scheduler outbound notifications are intentionally fail-closed until a
    durable, idempotent outbox/claim path exists; scheduled runs persist a zero-attempt
    notification summary with `skip_reason=durable_outbox_required`
  - retained February 14 E2E, performance, DR, and security reports are historical
    evidence, not validation of the current release candidate; the old security report
    is specifically invalid because its permissive threshold allowed a non-zero audit

## Production Gate Automation

- Scheduled gate workflow: `.github/workflows/production-gates.yml` (weekly + manual)
- Gate bundle includes:
  - Security gate (`bandit`, `pip-audit`, authz tests)
  - Backup/restore disaster recovery drill
  - Synthetic performance gate for core observability APIs
- Report artifacts are published under `docs/reports/security`, `docs/reports/dr`, and `docs/reports/perf`.

## Core Capabilities

| Capability | Status | Primary Endpoints |
|---|---|---|
| Fleet observability across deployments | API beta; authenticated operator console alpha | `GET /api/v1/observability/dashboard/fleet`, `/console/` |
| Session lifecycle management | Shipped | `POST /api/v1/observability/sessions`, `PATCH /api/v1/observability/sessions/{session_id}`, `GET /api/v1/observability/sessions/active` |
| Anomaly detection and triage | Shipped | `POST /api/v1/observability/detectors/run`, `GET /api/v1/observability/anomalies`, `GET /api/v1/observability/anomalies/groups` |
| Budget policy evaluation and control requests | Backend beta; no runtime delivery | `POST /api/v1/observability/budget-policies`, `POST /api/v1/observability/policies/evaluate`, `GET /api/v1/observability/budget-policies/events` |
| Approval workflow for shutdown requests | Backend beta; no runtime acknowledgement | `POST /api/v1/observability/policy-approvals`, `POST /api/v1/observability/policy-approvals/{approval_id}/decision` |
| Multi-agent delegation tracing | Shipped | `POST /api/v1/observability/delegations`, `GET /api/v1/observability/chains/{trace_id}` |
| Memory consistency monitoring | Shipped | `POST /api/v1/observability/memory/snapshots/batch`, `GET /api/v1/observability/memory/consistency` |
| Cost analytics and risk insights | Shipped | `GET /api/v1/observability/costs/summary`, `GET /api/v1/observability/insights/risk` |
| Runtime operations and scheduler visibility | Backend beta; durable database-fenced; scheduled outbound delivery disabled pending outbox | `POST /api/v1/observability/operations/run`, `GET /api/v1/observability/operations/status`, `GET /api/v1/observability/operations/runs` |
| Governance audit and SIEM export | Shipped | `GET /api/v1/observability/audit/events`, `POST /api/v1/observability/exports/siem` |

## Architecture

```text
src/
├── api/
│   ├── main.py                  # FastAPI app, middleware, health/metrics
│   └── routers/
│       ├── auth.py              # API-key-to-browser-session exchange
│       ├── console.py           # Same-origin production console assets
│       ├── traces.py            # Trace APIs
│       └── observability.py     # Fleet/session/runtime/policy APIs
├── cli/
│   ├── trace_viewer.py
│   └── production_preflight.py
├── models/                      # SQLAlchemy models (trace + observability domains)
├── services/
│   ├── observability_runtime.py # Detectors + policy evaluation/actions
│   ├── operations_scheduler.py  # Background runtime control loop
│   ├── notifications.py         # Webhook/Slack/PagerDuty dispatch
│   └── production_preflight.py  # Deployment readiness checks
├── tracing/                     # Tracer/context/provider wrappers + storage
├── security.py                  # API auth, RBAC, org scope enforcement
├── rate_limit.py                # In-memory request limiter with guardrails
└── config.py                    # Environment-backed settings
web/                             # React/TypeScript operator console
```

## Getting Started (Local)

### 1. Prerequisites

- Python `>=3.11`
- Docker (for local Postgres + Redis)

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cd web
npm ci
npm run build
cd ..
```

### 3. Start dependencies

```bash
docker compose -f docker/docker-compose.yml up -d db redis
```

### 4. Configure and migrate

```bash
cp .env.example .env
alembic upgrade head
```

The example environment connects to the Compose PostgreSQL service through
`localhost:5434`. List-valued settings accept a single value, comma-delimited values,
or a JSON array.

### 5. Run API

```bash
uvicorn src.api.main:app --reload
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- Operator console: `http://127.0.0.1:8000/console/`

The example `API_KEYS` entry uses `replace-me` as an explicitly local-only credential.
Enter that value on the console sign-in screen, select the `acme` organization, and
replace the credential before any shared or non-development use. The example disables
the browser cookie `Secure` flag only so plain-HTTP localhost works; production preflight
requires `BROWSER_SESSION_COOKIE_SECURE=true`.

## Docker Runtime

Build image:

```bash
docker build -f docker/Dockerfile .
```

The image and security gate consume the same hash-checked runtime and build artifacts at
`requirements/production.lock` and `requirements/build.lock`. The builder creates the
application wheel with PEP 517 isolation disabled, so no undeclared build dependency can
be resolved. Local editable development installation remains `pip install -e ".[dev]"`;
it does not modify either Docker lock.

Container startup guard rails:

- `MIGRATE_ON_START` toggles migration-at-boot behavior
- `PREFLIGHT_ON_START` runs deployment readiness checks before app startup
- `PREFLIGHT_STRICT` controls fail-fast behavior when preflight returns failures

## CLI Commands

- `ai-trace`: trace inspection CLI
- `ai-trace-preflight`: production readiness preflight checks
- `python scripts/production_preflight.py`: script wrapper for preflight checks

## API Surface

### Browser session APIs

- `POST /api/v1/auth/browser/sessions`
- `GET /api/v1/auth/browser/session`
- `DELETE /api/v1/auth/browser/session`

The API key is exchanged once for a database-backed, `HttpOnly`, same-site session
cookie. Unsafe cookie-authenticated requests also require the matching CSRF token.

### Trace APIs

- `GET /api/v1/traces`
- `GET /api/v1/traces/metrics/summary`
- `GET /api/v1/traces/{trace_id}`
- `GET /api/v1/traces/{trace_id}/reasoning`
- `GET /api/v1/traces/idea/{idea_id}/history`
- `GET /api/v1/traces/export/{trace_id}/json`

### Observability APIs

- `POST /api/v1/observability/deployments`
- `GET /api/v1/observability/deployments`
- `POST /api/v1/observability/sessions`
- `PATCH /api/v1/observability/sessions/{session_id}`
- `GET /api/v1/observability/sessions/active`
- `POST /api/v1/observability/actions/batch`
- `POST /api/v1/observability/delegations`
- `POST /api/v1/observability/memory/snapshots/batch`
- `POST /api/v1/observability/budget-policies`
- `GET /api/v1/observability/budget-policies`
- `GET /api/v1/observability/budget-policies/events`
- `POST /api/v1/observability/policy-approvals`
- `POST /api/v1/observability/policy-approvals/{approval_id}/decision`
- `GET /api/v1/observability/policy-approvals`
- `POST /api/v1/observability/policies/evaluate`
- `POST /api/v1/observability/policies/simulate`
- `POST /api/v1/observability/anomalies`
- `PATCH /api/v1/observability/anomalies/{anomaly_id}`
- `GET /api/v1/observability/anomalies`
- `GET /api/v1/observability/anomalies/{anomaly_id}`
- `GET /api/v1/observability/anomalies/groups`
- `POST /api/v1/observability/anomalies/groups/status`
- `POST /api/v1/observability/detectors/run`
- `POST /api/v1/observability/operations/run`
- `GET /api/v1/observability/operations/status`
- `GET /api/v1/observability/operations/runs`
- `GET /api/v1/observability/operations/runs/{run_id}`
- `GET /api/v1/observability/audit/events`
- `POST /api/v1/observability/exports/siem`
- `GET /api/v1/observability/dashboard/fleet`
- `GET /api/v1/observability/activation/status`
- `GET /api/v1/observability/costs/summary`
- `GET /api/v1/observability/insights/risk`
- `GET /api/v1/observability/memory/consistency`
- `GET /api/v1/observability/chains/{trace_id}`
- `GET /api/v1/observability/dashboard/ui`

Anomaly endpoints support optional `deployment_id` filtering for targeted triage.
The legacy embedded dashboard remains available only when authentication is disabled.
Authenticated environments redirect that route to the same-origin `/console/` app,
which supports fleet activation, anomaly triage, trace/span drill-down, retry states,
and session-attributed CSRF-protected mutations.

ACTIVE-session projections accept at most five minutes of client clock skew. Session
starts, heartbeats, and action events farther in the future are rejected with HTTP 422,
and historical rows beyond that bound are excluded from active-fleet totals.

### Platform Ops APIs

- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`
- `GET /docs`

`/metrics` and `/api/v1/observability/operations/status` require a global
administrator credential when API authentication is enabled and expose only
aggregate, tenant-free scheduler state.

## Notification Routing

Manually triggered runtime operations and SIEM exports support mixed target formats:

- Generic webhook: `https://hooks.example.com/ai-trace`
- Slack webhook URL: `https://hooks.slack.com/services/...`
- Slack prefixed target: `slack:https://hooks.slack.com/services/...`
- PagerDuty routing key: `pagerduty:<routing_key>`

For SIEM export payloads, `notification_targets` is preferred. Legacy `target_webhook` remains supported.
The background scheduler does not perform outbound delivery. Even when
`OBSERVABILITY_SCHEDULER_ENABLE_NOTIFICATIONS=true`, it fails closed and records
`durable_outbox_required`; this flag is reserved until a transactional outbox ships.

## Configuration

Configure with `.env` (see `.env.example`).

### Security and tenancy

- `API_AUTH_ENABLED`
- `API_KEY_HEADER`
- `API_TENANT_HEADER`
- `API_REQUIRE_TENANT_HEADER`
- `API_KEYS` (`token:subject:role1|role2:org1|org2`, `*` org allowed)
- `API_RATE_LIMIT_ENABLED`
- `API_RATE_LIMIT_REQUESTS_PER_WINDOW`
- `API_RATE_LIMIT_WINDOW_SECONDS`
- `API_RATE_LIMIT_PER_PATH`
- `API_RATE_LIMIT_MAX_KEYS`
- `BROWSER_SESSION_COOKIE_SECURE` (must be `true` in production)
- `BROWSER_SESSION_TTL_MINUTES`
- `BROWSER_CSRF_HEADER`
- `OPERATOR_CONSOLE_DIST_DIR`

The console shell and hashed static assets are public so the sign-in screen can load;
all operational data APIs require an authenticated session. Unsafe browser requests
also require the configured CSRF header.

### Database capacity

- `DATABASE_POOL_SIZE`
- `DATABASE_MAX_OVERFLOW`

Size the database pool against the deployment's worker count, database connection
budget, and measured request concurrency. The August 27 performance profile used a
24-connection pool with no overflow for 24 concurrent requests; this is validation
evidence for that profile, not a universal production sizing rule.

### Scheduler and runtime controls

- `OBSERVABILITY_SCHEDULER_ENABLED`
- `OBSERVABILITY_SCHEDULER_ORG_IDS`
- `OBSERVABILITY_SCHEDULER_INTERVAL_SECONDS`
- `OBSERVABILITY_SCHEDULER_LEASE_SECONDS`
- `OBSERVABILITY_SCHEDULER_RUN_DETECTORS`
- `OBSERVABILITY_SCHEDULER_RUN_POLICIES`
- `OBSERVABILITY_SCHEDULER_EXECUTE_POLICY_ACTIONS`
- `OBSERVABILITY_SCHEDULER_ENABLE_NOTIFICATIONS` (reserved; must remain `false`)
- `OBSERVABILITY_DETECTOR_ANOMALY_DEDUPE_WINDOW_MINUTES`
- `OBSERVABILITY_DETECTOR_ANOMALY_REOPEN_ACKNOWLEDGED`

### Notifications

- `OBSERVABILITY_NOTIFICATION_WEBHOOKS`
- `OBSERVABILITY_NOTIFICATION_SLACK_WEBHOOKS`
- `OBSERVABILITY_NOTIFICATION_PAGERDUTY_ROUTING_KEYS`
- `OBSERVABILITY_NOTIFICATION_MIN_SEVERITY`
- `OBSERVABILITY_NOTIFICATION_ONLY_ON_ACTIONABLE`
- `OBSERVABILITY_NOTIFICATION_TIMEOUT_SECONDS`
- `OBSERVABILITY_NOTIFICATION_MAX_ATTEMPTS`
- `OBSERVABILITY_NOTIFICATION_RETRY_BACKOFF_SECONDS`

### Shutdown safety

- `OBSERVABILITY_SHUTDOWN_REQUIRES_APPROVAL`
- `OBSERVABILITY_SHUTDOWN_APPROVAL_MAX_AGE_MINUTES`

### Container startup guards

- `MIGRATE_ON_START`
- `PREFLIGHT_ON_START`
- `PREFLIGHT_STRICT`

## Production Readiness Checklist

1. Enable API auth, tenant enforcement, request rate limiting, and HTTPS-only browser
   session cookies.
2. Use managed Postgres/Redis with backups, restore drills, and secret rotation.
3. Run migrations as part of deployment rollout.
4. Scope scheduler orgs explicitly. Configure notification channels only for manual
   runtime/SIEM dispatch, and keep scheduler outbound notifications disabled until the
   durable outbox ships.
5. Require approval for shutdown control requests.
6. Wire `/health/live`, `/health/ready`, and `/metrics` into orchestration and alerting.
7. Export SIEM bundles into security analytics workflows.
8. Build the console into `OPERATOR_CONSOLE_DIST_DIR`, then run deployment preflight
   checks (`ai-trace-preflight`) before release.
9. Validate release path with full e2e (`./scripts/run_full_e2e.sh`).
10. Run production gates (`./scripts/security_gate.sh`, `./scripts/backup_restore_drill.sh`, `./scripts/run_perf_gate.sh`).
11. Probe `/console/` on the exact image and verify its CSP, no-store HTML policy,
    immutable hashed assets, rendered application identity, and sign-in journey.

## Quality Gates

```bash
ai-trace-preflight
ruff check --select F .
pyright
pytest -q -p pytest_cov -p pytest_asyncio
./scripts/security_gate.sh
./scripts/backup_restore_drill.sh
./scripts/run_perf_gate.sh
docker build -f docker/Dockerfile .
./scripts/run_full_e2e.sh
```

## Security and Governance Notes

- API auth + RBAC + tenant scope enforcement are available and should be enabled in production.
- Browser sessions store only token hashes, are tenant/role scoped at creation, require
  CSRF validation for unsafe methods, and attribute mutations to the authenticated subject.
- Shutdown control requests can be approval-gated and audited through policy-approval APIs.
- System-level audit events are persisted and queryable for governance and incident review.
- SIEM export supports anomaly, policy, operations, and audit bundles for external retention.

## Roadmap Direction

Near-term focus areas:

- Acknowledged runtime adapters for throttle and shutdown delivery
- Durable, idempotent scheduled notification delivery
- Hosted console deployment, public identity, and customer-journey evidence
- SSO, user provisioning, and API-key lifecycle administration
- Longer-horizon anomaly baselines and seasonality-aware detection
- Adaptive suppression controls (beyond static dedupe windows)

## License

MIT
