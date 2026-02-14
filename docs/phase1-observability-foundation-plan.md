# AI Trace Phase 1 Foundation Plan + Phase 2 Runtime Control + Phase 3 Production Hardening Addendum

**Document date:** February 14, 2026  
**Execution window:** February 17, 2026 to February 28, 2026  
**Scope:** Build the production foundation for fleet/session/delegation observability before dashboard UI polish.
**Implementation status:** Phase 1 + Phase 2 + Phase 3 production hardening + deep trace API/storage integration cleanup + strict CI type-gate stabilization completed on February 14, 2026 (accelerated delivery)

## Implementation Log (Completed)

- Added Alembic revisions:
  - `alembic/versions/002_observability_enums.py`
  - `alembic/versions/003_observability_core_tables.py`
  - `alembic/versions/004_trace_dimension_linking.py`
  - `alembic/versions/005_observability_operation_runs.py`
  - `alembic/versions/006_policy_action_approvals.py`
  - `alembic/versions/007_system_audit_events.py`
- Added observability ORM domain model:
  - `src/models/observability.py`
- Linked observability dimensions into tracing model + tracer context:
  - `src/models/trace.py`
  - `src/tracing/context.py`
  - `src/tracing/tracer.py`
  - `src/tracing/types.py`
  - `src/tracing/storage/postgres.py`
- Added full observability API router:
  - `src/api/routers/observability.py`
  - router wiring in `src/api/routers/__init__.py` and `src/api/main.py`
- Validation completed:
  - Migration replay in isolated virtualenv: `upgrade head -> downgrade 001 -> upgrade head` succeeded
  - Endpoint smoke checks succeeded for deployment/session/action/delegation/anomaly/dashboard/cost/memory/anomaly-list/chains paths
  - Automated tests pass: `60 passed` (`tests/integration/test_observability_api.py`, `tests/integration/test_traces_api.py`, `tests/unit/test_cli_production_preflight.py`, `tests/unit/test_observability_router_helpers.py`, `tests/unit/test_tracing.py`, `tests/unit/test_tracing_decorators.py`, `tests/unit/test_observability_runtime.py`, `tests/unit/test_notifications.py`, `tests/unit/test_security.py`, `tests/unit/test_rate_limit.py`, `tests/unit/test_time_utils.py`, `tests/unit/test_operations_scheduler.py`, `tests/unit/test_production_preflight.py`)
  - Trace API/storage cleanup shipped:
    - `GET /api/v1/traces/metrics/summary` implemented with storage-backed aggregates
    - trace/span timestamp normalization to naive UTC in storage layer
    - trace metrics `from/to` query timestamps normalized to naive UTC
    - span persistence fixed for `assistant_response` and `output_data`
    - list-traces detached-instance bug fixed via eager span loading
    - list-traces `total` now honors `idea_id` and `correlation_id` filters
    - `Idea` model enum mapping aligned with migration enum types (`source_type`, `idea_status`)
  - Release-path hardening shipped:
    - CI Postgres service image aligned to `pgvector/pgvector:pg16`
    - CI now verifies migration replay (`downgrade 001 -> upgrade head`)
    - repeatable local e2e runner added: `scripts/run_full_e2e.sh`
    - shared UTC normalization utility added and wired across API/service/storage (`src/utils/time.py`)
    - strict pyright CI gate now green (`0 errors`) with high-signal diagnostics
    - same-request action-ingest idempotency guard added for duplicate `client_event_id` handling
    - scheduler org-fault isolation added (`last_tick_failures`) so one org failure no longer blocks other org runs
    - risk insights endpoint added (`GET /api/v1/observability/insights/risk`) for current-vs-previous window drift signals
    - cost burn-rate projection added in summary APIs (`projected_daily_cost_usd`, per-policy `projected_exhaustion_at`)
    - active-session endpoint enrichment added (`latest_action_type`, `latest_action_name`, `latest_action_resource`)
    - dashboard UI integrated with active-session feed and risk signal panel
    - operations status + `/metrics` now publish computed scheduler health (`healthy`/`degraded`/`stopped`/`disabled`)
    - duplicate UTC wrapper cleanup completed in API/runtime service paths
    - container hardening shipped:
      - non-root Docker runtime user
      - startup entrypoint with optional migration gate (`MIGRATE_ON_START`)
      - `.dockerignore` for leaner release artifacts
      - API healthcheck aligned to `/health/live`
    - CI now includes Docker build smoke validation
    - policy simulation/replay endpoint shipped (`POST /api/v1/observability/policies/simulate`) with rollback-only execution and aggregate projection outputs
    - policy cooldown historical-evaluation bug fixed (`triggered_at <= as_of`) so replay windows are temporally correct
    - runtime notification transport expanded to multi-channel dispatch (webhook + Slack + PagerDuty) with channel-aware payload shaping and per-channel delivery stats
    - scheduler and API runtime notification dispatch paths unified on the multi-channel notification engine
    - SIEM export delivery path upgraded from webhook-only to generic notification target routing (`notification_targets` with legacy `target_webhook` compatibility)
    - notification unit coverage extended for cross-channel routing and payload dispatch behavior
    - integration coverage extended for policy simulation side-effect guarantees and SIEM export target validation
    - detector anomaly dedup/suppression shipped with repeated-trigger aggregation metadata and optional ACK->OPEN auto-reopen controls
    - detector/runtime config extended with `anomaly_dedupe_window_minutes` + `anomaly_reopen_acknowledged` across API, scheduler, and env settings
    - actionable-only notification gating shipped (`OBSERVABILITY_NOTIFICATION_ONLY_ON_ACTIONABLE`) to suppress empty detector/policy runs
    - severity-threshold notification gating shipped (`OBSERVABILITY_NOTIFICATION_MIN_SEVERITY`) to prevent low-signal runtime alerts
    - grouped anomaly backlog endpoint shipped (`GET /api/v1/observability/anomalies/groups`) with status counts and occurrence rollups
    - grouped anomaly triage update endpoint shipped (`POST /api/v1/observability/anomalies/groups/status`) so operators can acknowledge/resolve grouped incidents in bulk
    - runtime notification gating and target-merge logic centralized in shared notification service helpers to remove router/scheduler drift risk
    - in-memory rate limiter hardened with stale-key pruning plus max-key eviction guardrails (`API_RATE_LIMIT_MAX_KEYS`) to prevent idle and high-cardinality memory growth under long-running API processes
    - deployment preflight checks added (`ai-trace-preflight`, `scripts/production_preflight.py`, `python -m src.cli.production_preflight`) with optional container startup enforcement (`PREFLIGHT_ON_START`, `PREFLIGHT_STRICT`)
    - integration coverage extended for repeated detector runs (`created_anomalies` then `deduplicated_anomalies`) on delegation-loop anomalies
    - full e2e runner re-validated after changes (`60 passed`, migration replay pass, second test pass)

## Phase 2 Runtime Control Addendum (Completed)

### Delivered runtime control capabilities

- Added runtime service engine:
  - `src/services/observability_runtime.py`
- Added policy enforcement loop (with auto actions):
  - policy evaluation (`alert`, `throttle`, `require_approval`, `shutdown`)
  - action-priority conflict resolution (`shutdown > require_approval > throttle > alert`)
  - policy event logging and cooldown handling
  - action-ingest auto policy evaluation (`evaluate_policies=true`)
- Added detector runtime:
  - API spike
  - cost spike
  - unusual resource access
  - memory divergence
  - delegation loop detection
- Added control/operations endpoints:
  - `GET /api/v1/observability/budget-policies/events`
  - `POST /api/v1/observability/policies/evaluate`
  - `POST /api/v1/observability/detectors/run`
  - `POST /api/v1/observability/operations/run`
  - `GET /api/v1/observability/operations/status`
  - `GET /api/v1/observability/operations/runs`
  - `GET /api/v1/observability/operations/runs/{run_id}`
  - `GET /api/v1/observability/dashboard/ui`
- Added optional background operations scheduler:
  - startup/shutdown lifecycle integration in FastAPI app
  - interval detectors/policy loop across configured orgs
  - webhook notification dispatch for detector/policy summaries
  - retry/backoff controls for webhook delivery
- Added persistent operations run logs:
  - table `observability_operation_runs`
  - scheduler and API control-loop run persistence for auditability
- Added tests:
  - expanded integration coverage in `tests/integration/test_observability_api.py`
  - new runtime helper unit tests in `tests/unit/test_observability_runtime.py`
  - new notification helper unit tests in `tests/unit/test_notifications.py`

### Validation evidence

- Full test suite: `60 passed`
- Live Postgres integration run: `tests/integration/test_observability_api.py` passed against isolated temporary Postgres with fresh `alembic upgrade head`
- Verified:
  - budget breach evaluation
  - policy event recording
  - detector execution endpoint
  - dashboard UI endpoint

## Phase 3 Production Hardening Addendum (Completed)

### Delivered hardening capabilities

- Added migration `alembic/versions/006_policy_action_approvals.py` and approval model:
  - `policy_action_approvals` table
  - approval status enum (`pending`, `approved`, `rejected`, `expired`)
- Added API authentication + tenant/RBAC controls:
  - `src/security.py`
  - API key auth and org-scope enforcement integrated in trace and observability routers
- Added shutdown safety approvals:
  - shutdown action execution now supports approval requirement gate
  - approval management endpoints:
    - `POST /api/v1/observability/policy-approvals`
    - `POST /api/v1/observability/policy-approvals/{approval_id}/decision`
    - `GET /api/v1/observability/policy-approvals`
- Added production operations primitives:
  - request-id and response-time headers middleware
  - liveness/readiness endpoints (`/health/live`, `/health/ready`)
  - JSON metrics endpoint (`/metrics`)
- Added deeper production controls:
  - configurable in-memory request rate limiting with response headers
  - system audit events table + API query endpoint
  - SIEM export endpoint for anomaly/policy/operations/audit bundles
- Added runtime operations observability enrichment:
  - scheduler health derivation in operations status + metrics APIs
  - active-session latest-action context in API responses and dashboard UI
- Added containerization hardening:
  - non-root runtime user in Docker image
  - deterministic startup entrypoint with migration switch
  - Docker build-context reduction via `.dockerignore`
- Added CI pipeline:
  - `.github/workflows/ci.yml` (lint, type-check, migration replay, tests, Docker build smoke)

### Validation evidence

- Fresh migration chain verified to head `006` on isolated Postgres container
- Integration test `tests/integration/test_observability_api.py` passes against migrated DB
- Full local suite: `60 passed`

## 1. Goal

Ship the minimum production-ready backend needed to support:
- deployment-level agent fleet visibility
- session lifecycle tracking
- multi-agent delegation chain tracing
- memory consistency event capture
- budget policy storage and breach event logging
- anomaly event ingestion and triage

This phase focuses on schema + APIs + tests, not frontend-heavy dashboard work.

## 2. In-Scope vs Out-of-Scope

### In Scope (Phase 1)

- New PostgreSQL schema objects for observability domain
- FastAPI router for observability endpoints
- Storage backend methods for new entities
- Contract tests for all new endpoints
- Baseline aggregation endpoints for fleet, sessions, costs, and anomalies

### Out of Scope (Phase 1)

- Streaming/WebSocket real-time UI
- ML-based anomaly models (rule thresholds only in this phase)
- Multi-region replication strategy

## 3. 2-Week Delivery Schedule

### Week 1 (Foundation)

| Date | Deliverable | Exit Criteria |
|---|---|---|
| Tue, Feb 17, 2026 | Contract freeze + migration scaffolding | Endpoint contracts approved; Alembic revision stubs created |
| Wed, Feb 18, 2026 | Revision `002` enums + constants | All enum types migrate up/down cleanly |
| Thu, Feb 19, 2026 | Revision `003` core tables + indexes | New tables + foreign keys + critical indexes in place |
| Fri, Feb 20, 2026 | Revision `004` trace linking + backfill helpers | Existing trace rows queryable with new dimensions |
| Sat, Feb 21, 2026 | Storage backend + model wiring | CRUD/read paths compile; typing clean |

### Week 2 (API + Validation)

| Date | Deliverable | Exit Criteria |
|---|---|---|
| Mon, Feb 23, 2026 | Write endpoints (deployment/session/action/memory/delegation/policy/anomaly) | POST/PATCH surfaces tested |
| Tue, Feb 24, 2026 | Read endpoints (fleet/session/cost/memory/anomaly/chain) | Aggregation endpoints tested |
| Wed, Feb 25, 2026 | Budget breach + anomaly triage flows | Policy event + anomaly status update working |
| Thu, Feb 26, 2026 | Integration tests + migration replay test | `alembic upgrade head`/`downgrade` verified |
| Fri, Feb 27, 2026 | Performance pass + docs update | p95 goals met for baseline queries |
| Sat, Feb 28, 2026 | Release candidate tag | Phase 1 checklist complete |

## 4. API Contract Set (Phase 1)

All endpoints use prefix: `/api/v1/observability`

### 4.1 Contract Conventions

- `datetime` values: ISO-8601 UTC.
- Pagination: `page` (default 1), `page_size` (default 50, max 200).
- Errors:
  - `400` invalid payload/filter
  - `404` not found
  - `409` uniqueness/idempotency conflict
  - `422` schema validation
  - `500` server error

### 4.2 Endpoint Index

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/deployments` | Register/update an agent deployment |
| `GET` | `/deployments` | List deployments |
| `POST` | `/sessions` | Start a session |
| `PATCH` | `/sessions/{session_id}` | End/update session status |
| `GET` | `/sessions/active` | Active sessions view |
| `POST` | `/actions/batch` | Ingest action events for a session |
| `POST` | `/delegations` | Record parent->child delegation edge |
| `POST` | `/memory/snapshots/batch` | Ingest memory consistency snapshots |
| `POST` | `/budget-policies` | Create policy for cost/token/action limits |
| `GET` | `/budget-policies` | List policies |
| `POST` | `/anomalies` | Ingest anomaly events |
| `PATCH` | `/anomalies/{anomaly_id}` | Acknowledge/resolve anomalies |
| `GET` | `/dashboard/fleet` | Fleet metrics + timeseries |
| `GET` | `/costs/summary` | Cost/token/action summary |
| `GET` | `/memory/consistency` | Divergence view for memory health |
| `GET` | `/chains/{trace_id}` | Multi-agent delegation graph for a trace |
| `GET` | `/anomalies` | Query anomaly backlog |
| `GET` | `/anomalies/groups` | Query grouped anomaly backlog with occurrence rollups |
| `POST` | `/anomalies/groups/status` | Bulk acknowledge/resolve anomaly groups by fingerprint |

### 4.3 Detailed Contracts

### `POST /deployments`

Request body:

```json
{
  "org_id": "acme",
  "deployment_key": "prod-us-east-1-main",
  "name": "Main Production",
  "environment": "prod",
  "runtime": "langgraph",
  "runtime_version": "0.4.2",
  "region": "us-east-1",
  "owner": "platform-team",
  "metadata": {}
}
```

Response `201`:

```json
{
  "id": "uuid",
  "org_id": "acme",
  "deployment_key": "prod-us-east-1-main",
  "name": "Main Production",
  "environment": "prod",
  "runtime": "langgraph",
  "runtime_version": "0.4.2",
  "region": "us-east-1",
  "owner": "platform-team",
  "is_active": true,
  "metadata": {},
  "created_at": "2026-02-17T18:00:00Z",
  "updated_at": "2026-02-17T18:00:00Z"
}
```

### `POST /sessions`

Request body:

```json
{
  "deployment_id": "uuid",
  "agent_id": "research-agent",
  "agent_instance_id": "pod-7f0a",
  "correlation_id": "uuid",
  "root_trace_id": "uuid",
  "parent_session_id": null,
  "workload_type": "customer_support",
  "tags": ["priority:high"],
  "metadata": {},
  "started_at": "2026-02-17T18:01:00Z"
}
```

Response `201`:

```json
{
  "id": "uuid",
  "deployment_id": "uuid",
  "agent_id": "research-agent",
  "status": "active",
  "started_at": "2026-02-17T18:01:00Z",
  "ended_at": null,
  "duration_ms": null,
  "last_activity_at": "2026-02-17T18:01:00Z"
}
```

### `PATCH /sessions/{session_id}`

Request body:

```json
{
  "status": "completed",
  "ended_at": "2026-02-17T18:03:00Z",
  "error_message": null
}
```

Response `200`: full `SessionResponse` with computed `duration_ms`.

### `POST /actions/batch`

Request body:

```json
{
  "session_id": "uuid",
  "events": [
    {
      "client_event_id": "uuid",
      "trace_id": "uuid",
      "span_id": "uuid",
      "action_type": "tool_call",
      "action_name": "web_search",
      "resource": "https://api.example.com/search",
      "provider": "openai",
      "model": "gpt-4.1",
      "input_tokens": 215,
      "output_tokens": 80,
      "estimated_cost_usd": 0.0011,
      "latency_ms": 380,
      "success": true,
      "occurred_at": "2026-02-17T18:01:20Z",
      "metadata": {}
    }
  ]
}
```

Response `202`:

```json
{
  "accepted": 1,
  "rejected": 0,
  "action_ids": ["uuid"],
  "errors": []
}
```

### `POST /delegations`

Request body:

```json
{
  "trace_id": "uuid",
  "parent_session_id": "uuid",
  "child_session_id": "uuid",
  "parent_action_id": "uuid",
  "status": "requested",
  "delegation_reason": "Need finance summary",
  "requested_capabilities": ["financial_analysis"],
  "started_at": "2026-02-17T18:02:00Z"
}
```

Response `201`: delegation edge record with generated `id`.

### `POST /memory/snapshots/batch`

Request body:

```json
{
  "session_id": "uuid",
  "snapshots": [
    {
      "trace_id": "uuid",
      "memory_namespace": "customer_profile",
      "memory_key": "user:12345",
      "content_hash": "sha256:...",
      "version_vector": {"agentA": 4, "agentB": 3},
      "source_sequence": 91,
      "consistency_state": "diverged",
      "divergence_score": 0.83,
      "observed_at": "2026-02-17T18:02:20Z",
      "metadata": {}
    }
  ]
}
```

Response `202`:

```json
{
  "accepted": 1,
  "rejected": 0,
  "snapshot_ids": ["uuid"],
  "errors": []
}
```

### `POST /budget-policies`

Request body:

```json
{
  "org_id": "acme",
  "policy_name": "prod daily budget",
  "scope_type": "deployment",
  "deployment_id": "uuid",
  "agent_id": null,
  "period_type": "day",
  "max_cost_usd": 500.0,
  "max_input_tokens": 10000000,
  "max_output_tokens": 4000000,
  "max_actions": 250000,
  "max_session_minutes": 180,
  "action_on_breach": "throttle",
  "throttle_rate": 50,
  "cooldown_seconds": 900,
  "notification_targets": ["slack:#ai-ops"],
  "metadata": {}
}
```

Response `201`: policy record with generated `id` and `status=active`.

### `POST /anomalies`

Request body:

```json
{
  "deployment_id": "uuid",
  "session_id": "uuid",
  "trace_id": "uuid",
  "action_id": "uuid",
  "anomaly_type": "api_spike",
  "severity": "high",
  "detector_name": "rule_calls_per_minute",
  "baseline_value": 120.0,
  "observed_value": 1440.0,
  "deviation_ratio": 12.0,
  "score": 0.98,
  "title": "12x API call spike",
  "description": "Session call volume exceeded baseline threshold",
  "detected_at": "2026-02-17T18:02:45Z",
  "metadata": {}
}
```

Response `201`: anomaly record with `status=open`.

### `PATCH /anomalies/{anomaly_id}`

Request body:

```json
{
  "status": "acknowledged",
  "note": "Investigating with on-call",
  "updated_by": "ops-user"
}
```

Response `200`: updated anomaly object.

### `GET /dashboard/fleet`

Query params:
- `org_id` (required)
- `deployment_id` (optional)
- `from` (required)
- `to` (required)
- `granularity` (`1m|5m|1h`, default `5m`)

Response `200`:

```json
{
  "window": {
    "from": "2026-02-17T17:00:00Z",
    "to": "2026-02-17T18:00:00Z",
    "granularity": "5m"
  },
  "totals": {
    "active_sessions": 83,
    "action_count": 14210,
    "error_rate": 0.013,
    "total_cost_usd": 97.42,
    "total_input_tokens": 17823000,
    "total_output_tokens": 4452000
  },
  "timeseries": [],
  "top_agents": [],
  "top_resources": []
}
```

### `GET /sessions/active`

Query params:
- `org_id` (required)
- `deployment_id` (optional)
- `agent_id` (optional)
- `page`/`page_size`

Response `200`:

```json
{
  "sessions": [
    {
      "id": "uuid",
      "deployment_id": "uuid",
      "agent_id": "research-agent",
      "status": "active",
      "started_at": "2026-02-17T17:45:00Z",
      "last_activity_at": "2026-02-17T17:59:58Z",
      "elapsed_ms": 899000
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50,
  "has_more": false
}
```

### `GET /chains/{trace_id}`

Response `200`:

```json
{
  "trace_id": "uuid",
  "nodes": [
    {"session_id": "uuid", "agent_id": "orchestrator", "status": "completed"},
    {"session_id": "uuid", "agent_id": "finance-agent", "status": "completed"}
  ],
  "edges": [
    {
      "delegation_id": "uuid",
      "parent_session_id": "uuid",
      "child_session_id": "uuid",
      "status": "completed",
      "requested_capabilities": ["financial_analysis"],
      "duration_ms": 810
    }
  ],
  "stats": {
    "depth": 2,
    "total_nodes": 2,
    "total_edges": 1,
    "failed_edges": 0
  }
}
```

### `GET /memory/consistency`

Query params:
- `org_id` (required)
- `deployment_id` (optional)
- `session_id` (optional)
- `from`/`to` (required)

Response `200`:

```json
{
  "summary": {
    "snapshot_count": 302,
    "diverged_count": 12,
    "divergence_rate": 0.0397
  },
  "hotspots": [],
  "recent": []
}
```

### `GET /costs/summary`

Query params:
- `org_id` (required)
- `deployment_id` (optional)
- `agent_id` (optional)
- `from`/`to` (required)

Response `200`:

```json
{
  "totals": {
    "cost_usd": 542.19,
    "input_tokens": 81200123,
    "output_tokens": 21098211,
    "action_count": 112093
  },
  "by_agent": [],
  "budgets": []
}
```

### `GET /anomalies`

Query params:
- `org_id` (required)
- `deployment_id` (optional)
- `status` (`open|acknowledged|resolved`, optional)
- `severity` (`low|medium|high|critical`, optional)
- `anomaly_type` (optional)
- `from`/`to` (required)
- `page`/`page_size`

Response `200`: paginated anomaly list.

### `GET /anomalies/groups`

Query params:
- `org_id` (required)
- `deployment_id` (optional)
- `status` (`open|acknowledged|resolved`, optional)
- `severity` (`low|medium|high|critical`, optional)
- `anomaly_type` (optional)
- `from`/`to` (required)
- `page`/`page_size`

Response `200`: paginated grouped anomaly list with:
- group `fingerprint` (`anomaly_type + deployment + normalized title`)
- `anomaly_count` (rows in group)
- `total_occurrences` (deduplicated trigger occurrence sum)
- lifecycle counts: `open_count`, `acknowledged_count`, `resolved_count`
- group timing: `first_detected_at`, `last_detected_at`

## 5. Alembic Migration Script Plan

## Revision `002_observability_enums.py`

Create enum types:
- `deployment_environment`: `dev`, `staging`, `prod`
- `session_status`: `active`, `idle`, `completed`, `failed`, `terminated`
- `action_type`: `llm_call`, `tool_call`, `delegation`, `memory_read`, `memory_write`, `network_call`, `policy_action`
- `memory_consistency_state`: `consistent`, `diverged`, `unknown`
- `budget_scope_type`: `org`, `deployment`, `agent`
- `budget_period_type`: `hour`, `day`, `month`
- `policy_action_type`: `alert`, `throttle`, `require_approval`, `shutdown`
- `policy_status`: `active`, `paused`
- `anomaly_type`: `api_spike`, `unusual_resource_access`, `memory_divergence`, `cost_spike`, `delegation_loop`
- `anomaly_severity`: `low`, `medium`, `high`, `critical`
- `anomaly_status`: `open`, `acknowledged`, `resolved`
- `delegation_status`: `requested`, `accepted`, `rejected`, `completed`, `failed`

Use same `DO $$ BEGIN IF NOT EXISTS ... END $$` pattern as revision `001`.

## Revision `003_observability_core_tables.py`

Create tables:
- `agent_deployments`
- `agent_sessions`
- `agent_actions`
- `delegation_edges`
- `memory_snapshots`
- `budget_policies`
- `budget_policy_events`
- `anomaly_events`

Critical constraints/indexes:
- `agent_deployments`: unique (`org_id`, `deployment_key`)
- `agent_sessions`: index (`deployment_id`, `status`), (`correlation_id`), (`agent_id`, `status`)
- `agent_actions`: unique (`session_id`, `client_event_id`) where `client_event_id IS NOT NULL`; index (`session_id`, `occurred_at`)
- `delegation_edges`: index (`trace_id`), (`parent_session_id`, `started_at`), (`child_session_id`, `started_at`)
- `memory_snapshots`: index (`session_id`, `observed_at`), (`memory_namespace`, `memory_key`, `observed_at`)
- `budget_policies`: index (`org_id`, `status`), (`deployment_id`, `status`), (`agent_id`, `status`)
- `budget_policy_events`: index (`policy_id`, `triggered_at`)
- `anomaly_events`: index (`detected_at`), (`status`, `severity`, `detected_at`), (`deployment_id`, `detected_at`)

Foreign key links:
- `agent_sessions.deployment_id -> agent_deployments.id`
- `agent_actions.session_id -> agent_sessions.id`
- `agent_actions.trace_id -> ai_traces.id`
- `agent_actions.span_id -> ai_trace_spans.id`
- `delegation_edges.parent_session_id -> agent_sessions.id`
- `delegation_edges.child_session_id -> agent_sessions.id`
- `delegation_edges.trace_id -> ai_traces.id`
- `memory_snapshots.session_id -> agent_sessions.id`
- `memory_snapshots.trace_id -> ai_traces.id`
- `budget_policies.deployment_id -> agent_deployments.id`
- `budget_policy_events.policy_id -> budget_policies.id`
- `budget_policy_events.session_id -> agent_sessions.id`
- `budget_policy_events.anomaly_event_id -> anomaly_events.id`
- `anomaly_events.deployment_id -> agent_deployments.id`
- `anomaly_events.session_id -> agent_sessions.id`
- `anomaly_events.trace_id -> ai_traces.id`
- `anomaly_events.action_id -> agent_actions.id`

## Revision `004_trace_dimension_linking.py`

Alter existing tables:
- `ai_traces` add nullable columns:
  - `org_id` (`String(255)`)
  - `deployment_id` (`UUID`, FK `agent_deployments.id`)
  - `session_id` (`UUID`, FK `agent_sessions.id`)
  - `agent_id` (`String(255)`)
- `ai_trace_spans` add nullable `session_id` (`UUID`, FK `agent_sessions.id`)

Add indexes:
- `ix_ai_traces_org_started` on (`org_id`, `started_at`)
- `ix_ai_traces_deployment_started` on (`deployment_id`, `started_at`)
- `ix_ai_traces_session_id` on (`session_id`)
- `ix_ai_trace_spans_session_started` on (`session_id`, `started_at`)

Backfill strategy:
- Copy dimension fields from `ai_traces.metadata` when present:
  - `org_id`, `deployment_id`, `session_id`, `agent_id`
- Keep all new columns nullable to avoid breaking existing ingestion clients.

## 6. Test Plan and Acceptance Criteria

### Migration Tests

- Clean DB: `alembic upgrade head` succeeds.
- Rollback: `alembic downgrade 001` succeeds.
- Re-upgrade succeeds without manual cleanup.

### API Contract Tests

- Positive/negative tests for every new endpoint.
- Idempotency test for `POST /actions/batch` with repeated `client_event_id`.
- Pagination/filter tests for `GET /sessions/active` and `GET /anomalies`.
- Aggregation correctness tests for `GET /dashboard/fleet` and `GET /costs/summary`.

### Performance Targets (Phase 1)

- `POST /actions/batch` p95 < 180ms for 100 events.
- `GET /dashboard/fleet` p95 < 800ms for 24h window on 1M action rows.
- `GET /sessions/active` p95 < 250ms on 100k sessions.

## 7. Definition of Done (Phase 1)

- All three revisions (`002`, `003`, `004`) merged and replay-tested.
- New observability router mounted in `src/api/main.py`.
- Endpoint contracts implemented with test coverage >= 85% in new modules.
- README and strategy docs updated with delivered status.
- No regression in existing `/api/v1/traces` and CLI paths.

## 8. Immediate Next Action After Approval

Start coding with this exact order:
1. Alembic revisions (`002` -> `003` -> `004`)
2. SQLAlchemy models for new tables
3. Storage backend methods
4. API router + pydantic schemas
5. Integration tests
