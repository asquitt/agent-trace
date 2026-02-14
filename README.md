# AI Trace

Agent observability and runtime governance platform for production AI-agent fleets.

AI Trace extends trace capture into an operational control plane: monitor multi-agent behavior, detect anomalies, enforce budget and safety policies, and maintain audit trails for every intervention.

## Why AI Trace

Most LLM observability tools stop at telemetry. AI Trace adds runtime controls:

- Fleet/session visibility across deployments
- Real-time anomaly detection with deduplication and grouped triage
- Budget policy enforcement with runtime actions (`alert`, `throttle`, `require_approval`, `shutdown`)
- Memory consistency monitoring across distributed sessions
- Multi-agent delegation chain tracing
- Continuous operations loop (manual + scheduler) with persistent run logs
- Audit and SIEM export paths for governance workflows

## Current Product Status

- Version: `0.2.0`
- Maturity: `Beta`
- Last validated: February 14, 2026
- Validation snapshot:
  - `ruff check --select F src tests scripts` passed
  - `pyright` passed (`0 errors`)
  - `pytest -q` passed (`54 passed, 2 skipped`) in lightweight local mode
  - `./scripts/run_full_e2e.sh` passed (`56 passed`, migration replay, double test pass)

## Core Capabilities

| Capability | Status | Primary Endpoints |
|---|---|---|
| Fleet observability across deployments | Shipped | `GET /api/v1/observability/dashboard/fleet`, `GET /api/v1/observability/dashboard/ui` |
| Session lifecycle management | Shipped | `POST /api/v1/observability/sessions`, `PATCH /api/v1/observability/sessions/{session_id}`, `GET /api/v1/observability/sessions/active` |
| Anomaly detection and triage | Shipped | `POST /api/v1/observability/detectors/run`, `GET /api/v1/observability/anomalies`, `GET /api/v1/observability/anomalies/groups` |
| Budget controls and actioning | Shipped | `POST /api/v1/observability/budget-policies`, `POST /api/v1/observability/policies/evaluate`, `GET /api/v1/observability/budget-policies/events` |
| Approval-gated shutdown safety | Shipped | `POST /api/v1/observability/policy-approvals`, `POST /api/v1/observability/policy-approvals/{approval_id}/decision` |
| Multi-agent delegation tracing | Shipped | `POST /api/v1/observability/delegations`, `GET /api/v1/observability/chains/{trace_id}` |
| Memory consistency monitoring | Shipped | `POST /api/v1/observability/memory/snapshots/batch`, `GET /api/v1/observability/memory/consistency` |
| Cost analytics and risk insights | Shipped | `GET /api/v1/observability/costs/summary`, `GET /api/v1/observability/insights/risk` |
| Runtime operations and scheduler visibility | Shipped | `POST /api/v1/observability/operations/run`, `GET /api/v1/observability/operations/status`, `GET /api/v1/observability/operations/runs` |
| Governance audit and SIEM export | Shipped | `GET /api/v1/observability/audit/events`, `POST /api/v1/observability/exports/siem` |

## Architecture

```text
src/
├── api/
│   ├── main.py                  # FastAPI app, middleware, health/metrics
│   └── routers/
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
```

### 3. Start dependencies

```bash
cd docker
docker compose up -d db redis
cd ..
```

### 4. Configure and migrate

```bash
cp .env.example .env
alembic upgrade head
```

### 5. Run API

```bash
uvicorn src.api.main:app --reload
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- Built-in dashboard UI: `http://127.0.0.1:8000/api/v1/observability/dashboard/ui`

## Docker Runtime

Build image:

```bash
docker build -f docker/Dockerfile .
```

Container startup guard rails:

- `MIGRATE_ON_START` toggles migration-at-boot behavior
- `PREFLIGHT_ON_START` runs deployment readiness checks before app startup
- `PREFLIGHT_STRICT` controls fail-fast behavior when preflight returns failures

## CLI Commands

- `ai-trace`: trace inspection CLI
- `ai-trace-preflight`: production readiness preflight checks
- `python scripts/production_preflight.py`: script wrapper for preflight checks

## API Surface

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
- `GET /api/v1/observability/anomalies/groups`
- `POST /api/v1/observability/detectors/run`
- `POST /api/v1/observability/operations/run`
- `GET /api/v1/observability/operations/status`
- `GET /api/v1/observability/operations/runs`
- `GET /api/v1/observability/operations/runs/{run_id}`
- `GET /api/v1/observability/audit/events`
- `POST /api/v1/observability/exports/siem`
- `GET /api/v1/observability/dashboard/fleet`
- `GET /api/v1/observability/costs/summary`
- `GET /api/v1/observability/insights/risk`
- `GET /api/v1/observability/memory/consistency`
- `GET /api/v1/observability/chains/{trace_id}`
- `GET /api/v1/observability/dashboard/ui`

Anomaly endpoints support optional `deployment_id` filtering for targeted triage.

### Platform Ops APIs

- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`
- `GET /docs`

## Notification Routing

AI Trace supports mixed target formats for runtime and SIEM notifications:

- Generic webhook: `https://hooks.example.com/ai-trace`
- Slack webhook URL: `https://hooks.slack.com/services/...`
- Slack prefixed target: `slack:https://hooks.slack.com/services/...`
- PagerDuty routing key: `pagerduty:<routing_key>`

For SIEM export payloads, `notification_targets` is preferred. Legacy `target_webhook` remains supported.

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

### Scheduler and runtime controls

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

### Shutdown safety

- `OBSERVABILITY_SHUTDOWN_REQUIRES_APPROVAL`
- `OBSERVABILITY_SHUTDOWN_APPROVAL_MAX_AGE_MINUTES`

### Container startup guards

- `MIGRATE_ON_START`
- `PREFLIGHT_ON_START`
- `PREFLIGHT_STRICT`

## Production Readiness Checklist

1. Enable API auth, tenant enforcement, and request rate limiting.
2. Use managed Postgres/Redis with backups, restore drills, and secret rotation.
3. Run migrations as part of deployment rollout.
4. Scope scheduler orgs explicitly and configure notification channels.
5. Require approval for shutdown policy actions.
6. Wire `/health/live`, `/health/ready`, and `/metrics` into orchestration and alerting.
7. Export SIEM bundles into security analytics workflows.
8. Run deployment preflight checks (`ai-trace-preflight`) before release.
9. Validate release path with full e2e (`./scripts/run_full_e2e.sh`).

## Quality Gates

```bash
ai-trace-preflight
ruff check --select F src tests scripts
pyright
pytest -q -p pytest_cov -p pytest_asyncio
docker build -f docker/Dockerfile .
./scripts/run_full_e2e.sh
```

## Security and Governance Notes

- API auth + RBAC + tenant scope enforcement are available and should be enabled in production.
- Shutdown actions can be approval-gated and audited through policy-approval APIs.
- System-level audit events are persisted and queryable for governance and incident review.
- SIEM export supports anomaly, policy, operations, and audit bundles for external retention.

## Roadmap Direction

Near-term focus areas:

- Longer-horizon anomaly baselines and seasonality-aware detection
- Adaptive suppression controls (beyond static dedupe windows)
- Broader enterprise sink integrations and standards-aligned telemetry export
- Feedback loops from operator triage outcomes into detector tuning

## License

MIT
