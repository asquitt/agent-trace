# Incident Response Runbook

Last updated: August 27, 2026

> Status: Frozen manual reference for a future host-owned integration. AI Trace has no current
> repository-owned production target, incident channel, alert routing, or on-call operation.

## Severity Levels

- Sev1: Full outage, policy engine unavailable, or data integrity risk.
- Sev2: Partial degradation, high error rates, delayed operations loop.
- Sev3: Minor degradation, non-critical feature impact.

## First 15 Minutes

1. Declare incident in on-call channel.
2. Assign incident commander and communications owner.
3. Verify health endpoints:
   - `/health/live`
   - `/health/ready`
   - `/metrics` using the global operational monitoring credential
4. Check alerts from `deploy/monitoring/prometheus-rules.yaml`.
5. Decide mitigation path:
   - rollback deployment
   - scale out
   - disable scheduler actions temporarily

## Containment Actions

- Toggle scheduler execution flags if runtime policy actions are unsafe.
- Enforce manual approval workflow for shutdown actions.
- Temporarily raise rate limits only with explicit incident commander approval.

## Recovery Validation

- Error rate back to SLO range.
- Scheduler health not degraded.
- No backlog growth in operations runs.
- Critical anomaly groups triaged or resolved.

## Postmortem Requirements

- Complete RCA within 48 hours.
- Define permanent fix and owner.
- Add regression test or operational guardrail.
- Update runbook/checklist where process failed.
