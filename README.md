# AI Trace

Production-focused agent observability and runtime governance platform.

AI Trace provides end-to-end traceability for autonomous agent systems and adds operational controls for cost, safety, and reliability across deployments.

## What You Get

- Trace, span, and reasoning-chain capture
- Fleet and session observability across deployments
- Runtime anomaly detection (API spikes, cost spikes, unusual resource access, memory divergence, delegation loops)
- Budget policy engine with runtime controls (`alert`, `throttle`, `require_approval`, `shutdown`)
- Multi-agent delegation chain tracing
- Continuous control loops (scheduler), notifications, and run audit logs
- Tenant-scoped API auth, RBAC, and shutdown approval workflow

## Release Status

**Current version:** `0.2.0`  
**Maturity:** Beta  
**Validation snapshot:** `19 passed, 1 skipped` (unit + integration in local environment)

## Core Capabilities

### 1) Runtime Visibility

- Deployments, sessions, actions, delegations, memory snapshots
- Fleet metrics, top agents/resources, cost/token summaries
- Active session inventory and session lifecycle tracking

### 2) Detection + Governance

- Rule-based detectors for behavioral/runtime anomalies
- Budget policies with automatic enforcement actions
- Policy conflict priority handling:
  - `shutdown > require_approval > throttle > alert`

### 3) Safety Controls

- Optional requirement for explicit approval before shutdown actions execute
- Approval API workflow:
  - create approval request
  - approve/reject decision
  - list and audit approval records

### 4) Operations Plane

- Manual control-loop execution endpoints
- Background scheduler for periodic detectors/policies
- Outbound webhook notifications with retry/backoff
- Persistent operation-run logs for manual and scheduled loops

### 5) Security and Isolation

- API key authentication (optional, configurable)
- RBAC roles: `viewer`, `operator`, `admin`
- Tenant scope enforcement by org (`X-Org-Id` + policy/org checks)

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
├── security.py                 # Auth + RBAC + tenant enforcement helpers
└── config.py                   # Environment-backed settings
```

## API Surface

### Trace APIs

- `GET /api/v1/traces`
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
  - `POST /api/v1/observability/detectors/run`
  - `POST /api/v1/observability/operations/run`
  - `GET /api/v1/observability/operations/status`
  - `GET /api/v1/observability/operations/runs`
  - `GET /api/v1/observability/operations/runs/{run_id}`
- Dashboards/Analytics:
  - `GET /api/v1/observability/dashboard/fleet`
  - `GET /api/v1/observability/costs/summary`
  - `GET /api/v1/observability/memory/consistency`
  - `GET /api/v1/observability/chains/{trace_id}`
  - `GET /api/v1/observability/dashboard/ui`

### Platform Health/Ops

- `GET /health`
- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`
- `GET /docs`

## Quick Start (Local Development)

```bash
# 1) Install
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2) Start infra
cd docker
docker compose up -d db redis
cd ..

# 3) Configure env
cp .env.example .env

# 4) Run migrations
alembic upgrade head

# 5) Start API
uvicorn src.api.main:app --reload
```

Open:

- API docs: `http://127.0.0.1:8000/docs`
- Observability dashboard: `http://127.0.0.1:8000/api/v1/observability/dashboard/ui`

## Configuration

Use `.env` (see `.env.example`).

### Security

- `API_AUTH_ENABLED`
- `API_KEY_HEADER`
- `API_TENANT_HEADER`
- `API_REQUIRE_TENANT_HEADER`
- `API_KEYS` format:
  - `token:subject:role1|role2:org1|org2`
  - Use `*` org for global admin keys

### Scheduler + Notifications

- `OBSERVABILITY_SCHEDULER_ENABLED`
- `OBSERVABILITY_SCHEDULER_ORG_IDS`
- `OBSERVABILITY_SCHEDULER_INTERVAL_SECONDS`
- `OBSERVABILITY_NOTIFICATION_WEBHOOKS`
- `OBSERVABILITY_NOTIFICATION_MAX_ATTEMPTS`
- `OBSERVABILITY_NOTIFICATION_RETRY_BACKOFF_SECONDS`

### Shutdown Safety

- `OBSERVABILITY_SHUTDOWN_REQUIRES_APPROVAL`
- `OBSERVABILITY_SHUTDOWN_APPROVAL_MAX_AGE_MINUTES`

## Production Deployment Checklist

1. Enable auth and tenant headers.
2. Provision managed Postgres and Redis.
3. Run migrations (`alembic upgrade head`) during deploy.
4. Configure scheduler org list and notification webhooks.
5. Enable readiness/liveness checks in orchestrator.
6. Configure alerting on:
   - `/health/ready != 200`
   - operation-run failures
   - notification delivery failures
7. Rotate API keys and store secrets in a vault/KMS.

## Quality Gates

```bash
ruff check src tests
pyright
pytest -q -p pytest_cov -p pytest_asyncio
```

GitHub Actions CI is included in:

- `.github/workflows/ci.yml`

## Notable Documents

- `docs/phase1-observability-foundation-plan.md`
- `/Users/demarioasquitt/Desktop/Projects/Entrepreneurial/explore/ai-trace-observability-platform-analysis.md`
- `/Users/demarioasquitt/Desktop/Projects/Entrepreneurial/explore/playbook-status.md`
- `/Users/demarioasquitt/Desktop/Projects/Entrepreneurial/STRATEGIC_ROADMAP.md`

## License

MIT
