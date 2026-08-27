# Production Hardening Runbook

Last updated: August 27, 2026

## Scope

This runbook operationalizes production hardening for AI Trace across deployment topology, resilience, and monitoring.

## Required Deployment Topology

1. API replicas: minimum 3 (`deploy/k8s/api-deployment.yaml`).
2. Autoscaling: HPA 3-20 replicas (`deploy/k8s/hpa.yaml`).
3. Disruption tolerance: PDB minAvailable=2 (`deploy/k8s/pdb.yaml`).
4. Network isolation: namespace-scoped ingress/egress (`deploy/k8s/networkpolicy.yaml`).
5. Externalized state: managed Postgres and Redis (HA, backups, multi-AZ).

## Security Controls

1. Non-root runtime user.
2. Read-only root filesystem + dropped Linux capabilities.
3. API auth + tenant header enforcement enabled.
4. Preflight startup guard enabled (`PREFLIGHT_ON_START=true`, `PREFLIGHT_STRICT=true`).
5. Browser sessions use HTTPS-only cookies (`BROWSER_SESSION_COOKIE_SECURE=true`).
6. The built image contains the exact operator console bundle served from `/console/`.

## Observability Controls

1. Blackbox readiness probes (`deploy/monitoring/blackbox-scrape-example.yaml`).
2. Prometheus alert rules (`deploy/monitoring/prometheus-rules.yaml`).
3. SLO definitions (`deploy/slo/slo-objectives.yaml`).
4. Runtime health surfaces: `/health/live`, `/health/ready`, `/metrics`.

## Release Gate Checklist

- [x] `ai-trace-preflight` passes in target environment.
- [x] The synthetic performance gate passes with the production threshold profile for
  the exact release candidate. Evidence: local ignored
  `docs/reports/perf/perf-gate-20260827T125448Z.json` (284 calls, zero failures,
  actions-batch p95 505ms, worst read p95 597ms). The measured profile set
  `DATABASE_POOL_SIZE=24` and `DATABASE_MAX_OVERFLOW=0` for concurrency 24; preserve or
  revalidate that capacity contract in the target environment.
- [x] `scripts/backup_restore_drill.sh` passes with matching before/after counts for the
  current migration graph. Evidence: local ignored
  `docs/reports/dr/backup-restore-drill-20260827T124805Z.json` (`before=after=1/1/1`).
- [x] Fail-closed security gate passes for the release candidate: `bandit`, the
  hash-locked production dependency audit, and endpoint security-contract tests.
  Evidence: local ignored `docs/reports/security/security-gate-20260827T125633Z.json`
  (69 exact locked dependencies, zero known vulnerabilities). Every future production
  finding requires remediation or explicit, unexpired risk acceptance under
  `docs/security/vulnerability-waivers.json`.
- [ ] Alert routes and on-call escalations are configured and tested in the target
  environment. References: `docs/runbooks/oncall-operations.md`,
  `docs/runbooks/incident-response.md`.
