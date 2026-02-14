# AI Trace

AI Trace is an agent observability and runtime governance platform for production agent fleets.

It combines trace/session visibility with active controls so operators can detect abnormal behavior, enforce budget/safety policies, and audit interventions across deployments.

## Product Scope

AI Trace extends classic LLM tracing into a control plane:

- Fleet and session observability across deployments
- Real-time anomaly detection for agent behavior
- Anomaly deduplication/suppression with repeated-trigger aggregation
- Memory consistency monitoring across distributed sessions
- Multi-agent delegation chain tracing
- Budget policy evaluation with runtime actions (`alert`, `throttle`, `require_approval`, `shutdown`)
- Operations scheduler with persistent run/audit logs
- SIEM export and operational notifications (webhook, Slack, PagerDuty)
- Actionable-only notification gating to suppress no-op detector runs
- Severity-threshold notification gating to suppress low-signal runtime events
- Grouped anomaly views with occurrence rollups for faster triage

## Release Status

- Version: `0.2.0`
- Maturity: `Beta`
- Last validation: February 14, 2026
- Validation evidence:
  - `ruff check --select F src tests` passed
  - `pyright` passed (`0 errors`)
  - `pytest -q` passed (`42 passed, 2 skipped`) in lightweight local run
  - `./scripts/run_full_e2e.sh` passed (`44 passed`, fresh DB, migration replay, double test pass)

## Architecture

```text
src/
├── api/
│   ├── main.py                 # FastAPI app, middleware, health/metrics
│   └── routers/
│       ├── traces.py           # Trace read/export APIs
│       └── observability.py    # Fleet/session/runtime/policy APIs
├── models/                     # SQLAlchemy models (trace + observability domains)
├── services/
│   ├── observability_runtime.py
│   ├── operations_scheduler.py
│   └── notifications.py
├── tracing/                    # Tracer/context/provider wrappers + storage backend
├── security.py                 # Auth + RBAC + tenant enforcement
└── config.py                   # Environment-backed settings
```

## API Surface

### Trace APIs

- `GET /api/v1/traces`
- `GET /api/v1/traces/metrics/summary`
- `GET /api/v1/traces/{trace_id}`
- `GET /api/v1/traces/{trace_id}/reasoning`
- `GET /api/v1/traces/idea/{idea_id}/history`
- `GET /api/v1/traces/export/{trace_id}/json`

### Observability APIs

- Deployments/Sessions/Actions/Delegations/Memory:
  - `POST /api/v1/observability/deployments`
  - `GET /api/v1/observability/deployments`
  - `POST /api/v1/observability/sessions`
  - `PATCH /api/v1/observability/sessions/{session_id}`
  - `GET /api/v1/observability/sessions/active`
  - `POST /api/v1/observability/actions/batch`
  - `POST /api/v1/observability/delegations`
  - `POST /api/v1/observability/memory/snapshots/batch`
- Policies/Approvals/Detectors/Operations:
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
  - `GET /api/v1/observability/anomalies/groups`
  - `POST /api/v1/observability/detectors/run`
  - `POST /api/v1/observability/operations/run`
  - `GET /api/v1/observability/operations/status`
  - `GET /api/v1/observability/operations/runs`
  - `GET /api/v1/observability/operations/runs/{run_id}`
  - `GET /api/v1/observability/audit/events`
  - `POST /api/v1/observability/exports/siem`
- Dashboards/Analytics:
  - `GET /api/v1/observability/dashboard/fleet`
  - `GET /api/v1/observability/costs/summary`
  - `GET /api/v1/observability/insights/risk`
  - `GET /api/v1/observability/memory/consistency`
  - `GET /api/v1/observability/chains/{trace_id}`
  - `GET /api/v1/observability/dashboard/ui`

Anomaly backlog endpoints support optional `deployment_id` query filtering for targeted triage.

### Platform Health/Ops

- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`
- `GET /docs`

## Quick Start (Local)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cd docker
docker compose up -d db redis
cd ..

cp .env.example .env
alembic upgrade head
uvicorn src.api.main:app --reload
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- Runtime dashboard UI: `http://127.0.0.1:8000/api/v1/observability/dashboard/ui`

## Notification Targets

AI Trace supports mixed target types in runtime and SIEM notifications:

- Generic webhook: `https://hooks.example.com/ai-trace`
- Slack webhook URL directly: `https://hooks.slack.com/services/...`
- Slack prefixed target: `slack:https://hooks.slack.com/services/...`
- PagerDuty routing key: `pagerduty:<routing_key>`

For SIEM exports, `notification_targets` is the preferred field. `target_webhook` remains supported as a legacy fallback.

## Key Configuration

Configure via `.env` (see `.env.example`).

### Security and Tenancy

- `API_AUTH_ENABLED`
- `API_KEY_HEADER`
- `API_TENANT_HEADER`
- `API_REQUIRE_TENANT_HEADER`
- `API_KEYS` (`token:subject:role1|role2:org1|org2`; `*` org allowed)
- `API_RATE_LIMIT_ENABLED`
- `API_RATE_LIMIT_REQUESTS_PER_WINDOW`
- `API_RATE_LIMIT_WINDOW_SECONDS`
- `API_RATE_LIMIT_PER_PATH`

### Scheduler and Runtime Control

- `OBSERVABILITY_SCHEDULER_ENABLED`
- `OBSERVABILITY_SCHEDULER_ORG_IDS`
- `OBSERVABILITY_SCHEDULER_INTERVAL_SECONDS`
- `OBSERVABILITY_SCHEDULER_RUN_DETECTORS`
- `OBSERVABILITY_SCHEDULER_RUN_POLICIES`
- `OBSERVABILITY_SCHEDULER_EXECUTE_POLICY_ACTIONS`
- `OBSERVABILITY_SCHEDULER_ENABLE_NOTIFICATIONS`
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

### Shutdown Safety

- `OBSERVABILITY_SHUTDOWN_REQUIRES_APPROVAL`
- `OBSERVABILITY_SHUTDOWN_APPROVAL_MAX_AGE_MINUTES`

## Production Checklist

1. Enable API auth, tenant enforcement, and rate limiting.
2. Use managed Postgres + Redis with backups and rotation.
3. Run migrations during deploy (`alembic upgrade head`).
4. Configure scheduler org scope and notification channels.
5. Wire readiness/liveness checks to orchestrator health gates.
6. Alert on scheduler degradation and operation run failures.
7. Export SIEM bundles to your security pipeline.
8. Rotate API keys and store secrets in KMS/vault.

## Quality Gates

```bash
ruff check --select F src tests
pyright
pytest -q -p pytest_cov -p pytest_asyncio
docker build -f docker/Dockerfile .
./scripts/run_full_e2e.sh
```

## Current Differentiators

- Runtime governance actions from policy breaches (not alert-only)
- Memory divergence and delegation-loop detection in the same control loop
- Policy simulation/replay endpoint for pre-production impact analysis
- Unified operations/audit logging for manual and scheduled controls
