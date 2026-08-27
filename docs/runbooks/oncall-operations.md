# On-Call Operations Runbook

Last updated: August 27, 2026

## On-Call Rotation

- Primary: owns initial response and mitigation.
- Secondary: escalation backup and verification.

## Shift Start Checklist

1. Confirm monitoring pipelines are green.
2. Confirm platform-monitoring and manual delivery routes to PagerDuty/Slack.
3. Confirm latest deployment and migration state.
4. Confirm latest backup/restore drill report is passing.
5. Confirm scheduler notification summaries show zero attempts and
   `durable_outbox_required` while automated delivery remains fail-closed.

## Alert Triage Priorities

1. `AITraceReadinessFailure` (critical)
2. `AITraceDeploymentUnavailable` (critical)
3. `AITracePodCrashLooping` (critical)
4. `AITraceHighProbeLatencyP95` (warning)

## Standard Checks

- `curl -sS http://<api-host>/health/live`
- `curl -sS http://<api-host>/health/ready`
- `curl -sS -H "X-API-Key: ${METRICS_API_KEY}" -H "X-Org-Id: platform-monitoring" http://<api-host>/metrics`

`/metrics` and `/api/v1/observability/operations/status` are global operational
surfaces. In authenticated environments they require a separately rotated `admin:*`
monitoring credential; never place that value in a command transcript or repository.

## Escalation

- Escalate Sev1 immediately to platform owner + security owner.
- Escalate unresolved Sev2 after 30 minutes.
- Escalate unresolved Sev3 after 4 hours.
