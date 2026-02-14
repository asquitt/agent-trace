# Secret Rotation Runbook

Last updated: February 14, 2026

## Scope

Rotates all AI Trace production secrets without downtime.

## Secrets in Scope

1. API keys (`API_KEYS`, API gateway credentials).
2. Database credentials (`DATABASE_URL`).
3. Redis credentials (`REDIS_URL`).
4. Notification integrations:
   - Slack webhook secrets
   - PagerDuty routing keys
   - SIEM outbound tokens
5. LLM provider keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

## Rotation Procedure

1. Generate new secret versions in KMS/Vault.
2. Update Kubernetes secrets (`ai-trace-secrets`, `ai-trace-api-keys`) with new versions.
3. Roll API deployment with maxUnavailable=0 rolling update.
4. Validate readiness and runtime control-loop health.
5. Revoke old secret versions.
6. Record rotation evidence in security log.

## Validation

- `/health/ready` returns 200 after rollout.
- `/metrics` scheduler health is `healthy` or expected non-`degraded` state.
- Notification delivery smoke test succeeds.
- Authenticated API requests succeed with newly rotated keys.

## Maximum Secret Age Targets

- API keys: 30 days
- DB/Redis credentials: 60 days
- Notification/third-party tokens: 60 days
- LLM provider keys: 30 days
