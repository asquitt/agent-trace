# AI Trace

Agent observability platform for tracing, monitoring, and governing autonomous agent fleets.

## Current Status (Feb 14, 2026)

AI Trace scope for Wealth Play #2 is now implemented as an MVP runtime control plane.

| Area | Status |
|---|---|
| Trace/span/reasoning capture | COMPLETE |
| API + CLI trace exploration | COMPLETE |
| Per-trace token/cost tracking | COMPLETE |
| Fleet/deployment dashboards | COMPLETE (API + built-in web UI) |
| Session management visualization | COMPLETE |
| Anomaly detection (10x spikes, unusual access, divergence, loops) | COMPLETE |
| Memory consistency monitoring | COMPLETE |
| Budget alerts + auto-shutdown policies | COMPLETE |
| Multi-agent delegation chain tracing | COMPLETE |
| Continuous ops loop + run audit logs | COMPLETE |

## Delivered Platform Capabilities

- Deployment, session, action, delegation, memory, anomaly, and policy schemas with migrations
- Fleet dashboard metrics API with timeseries, top agents, and top resources
- Session lifecycle tracking and active session inventory
- Multi-agent chain tracing (`Agent A -> Agent B -> ...`) via delegation graph endpoint
- Rule-based detector runtime:
  - API call spike detector
  - Cost spike detector
  - Unusual resource access detector
  - Memory divergence detector
  - Delegation loop detector
- Runtime policy engine with auto control actions:
  - `alert`
  - `throttle`
  - `require_approval`
  - `shutdown`
- Policy conflict guardrail: higher-severity actions win (`shutdown > require_approval > throttle > alert`)
- Policy event logging and on-demand policy evaluation
- Optional background scheduler for continuous detectors/policy loops across configured orgs
- Optional outbound webhook notifications for detector/policy events
- Persistent operation run logs for manual and scheduled control-loop runs
- Built-in dashboard page at `/api/v1/observability/dashboard/ui` with live refresh

## Quick Start

```bash
# Install
pip install -e .

# Start local infra
cd docker && docker compose up -d

# Run migrations
alembic upgrade head

# Start API
uvicorn src.api.main:app --reload

# Trace CLI
ai-trace list
ai-trace show <trace_id>
```

Open:
- API docs: `http://127.0.0.1:8000/docs`
- Runtime dashboard UI: `http://127.0.0.1:8000/api/v1/observability/dashboard/ui`

## API Endpoints

Trace APIs:
- `GET /api/v1/traces`
- `GET /api/v1/traces/{id}`
- `GET /api/v1/traces/{id}/reasoning`
- `GET /api/v1/traces/idea/{idea_id}/history`
- `GET /api/v1/traces/export/{id}/json`

Observability APIs:
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
- `POST /api/v1/observability/policies/evaluate`
- `POST /api/v1/observability/anomalies`
- `PATCH /api/v1/observability/anomalies/{anomaly_id}`
- `GET /api/v1/observability/anomalies`
- `POST /api/v1/observability/detectors/run`
- `POST /api/v1/observability/operations/run`
- `GET /api/v1/observability/operations/status`
- `GET /api/v1/observability/operations/runs`
- `GET /api/v1/observability/operations/runs/{run_id}`
- `GET /api/v1/observability/dashboard/fleet`
- `GET /api/v1/observability/costs/summary`
- `GET /api/v1/observability/memory/consistency`
- `GET /api/v1/observability/chains/{trace_id}`
- `GET /api/v1/observability/dashboard/ui`

## Validation Snapshot

- Full unit/integration suite: `15 passed, 1 skipped`
- Live integration verification with Docker Postgres:
  - `tests/integration/test_observability_api.py` passes end-to-end
  - Policy shutdown enforcement, policy event logging, and operation-run log APIs validated

## Competitive Positioning Snapshot (Feb 14, 2026)

What market leaders now cover well:
- Trace + session observability
- Token/cost analytics
- Alerting and dashboards

Where AI Trace currently differentiates:
- Runtime policy actioning (`throttle`/`require_approval`/`shutdown`)
- Memory divergence monitoring integrated into the same governance loop
- Multi-agent delegation chain observability tied to policy/budget controls

Primary references:
- Datadog LLM Observability: [https://docs.datadoghq.com/llm_observability/](https://docs.datadoghq.com/llm_observability/)
- Langfuse sessions/cost/alerts/agent graphs: [https://langfuse.com/docs/observability/features/sessions](https://langfuse.com/docs/observability/features/sessions)
- LangSmith observability: [https://docs.langchain.com/langsmith/observability-quickstart](https://docs.langchain.com/langsmith/observability-quickstart)
- OpenTelemetry GenAI conventions: [https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)

## Scheduler + Notifications

Set these env vars to enable continuous runtime operations:

```bash
OBSERVABILITY_SCHEDULER_ENABLED=true
OBSERVABILITY_SCHEDULER_ORG_IDS=acme,contoso
OBSERVABILITY_SCHEDULER_INTERVAL_SECONDS=60
OBSERVABILITY_NOTIFICATION_WEBHOOKS=https://hooks.example.com/ops
```

Optional controls:
- `OBSERVABILITY_SCHEDULER_RUN_DETECTORS`
- `OBSERVABILITY_SCHEDULER_RUN_POLICIES`
- `OBSERVABILITY_SCHEDULER_EXECUTE_POLICY_ACTIONS`
- `OBSERVABILITY_SCHEDULER_ENABLE_NOTIFICATIONS`
- `OBSERVABILITY_NOTIFICATION_MAX_ATTEMPTS`
- `OBSERVABILITY_NOTIFICATION_RETRY_BACKOFF_SECONDS`

## Architecture

```text
src/
├── tracing/    # Tracer, context propagation, provider wrappers
├── models/     # SQLAlchemy models (trace + observability domains)
├── api/        # FastAPI routers/endpoints
├── services/   # Runtime detector + policy engine services
└── cli/        # Terminal trace viewer
```

## Strategy Docs

- `/Users/demarioasquitt/Desktop/Projects/Entrepreneurial/explore/ai-trace-observability-platform-analysis.md`
- `/Users/demarioasquitt/Desktop/Projects/Entrepreneurial/explore/playbook-status.md`
- `/Users/demarioasquitt/Desktop/Projects/Entrepreneurial/STRATEGIC_ROADMAP.md`
- `/Users/demarioasquitt/Desktop/Projects/Entrepreneurial/PROJECTS.md`
- `/Users/demarioasquitt/Desktop/Projects/Entrepreneurial/traceability/docs/phase1-observability-foundation-plan.md`

## License

MIT
