# Production Hardening Runbook

Last updated: February 14, 2026 (post-gate validation)

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

## Observability Controls

1. Blackbox readiness probes (`deploy/monitoring/blackbox-scrape-example.yaml`).
2. Prometheus alert rules (`deploy/monitoring/prometheus-rules.yaml`).
3. SLO definitions (`deploy/slo/slo-objectives.yaml`).
4. Runtime health surfaces: `/health/live`, `/health/ready`, `/metrics`.

## Release Gate Checklist

- [x] `ai-trace-preflight` passes in target environment.
- [x] `scripts/run_perf_gate.sh` passes with production threshold profile.
  Evidence: `docs/reports/perf/perf-gate-20260214T194058Z.json` (`status=passed`, `error_rate=0.0`).
- [x] `scripts/backup_restore_drill.sh` passes with matching before/after counts.
  Evidence: `docs/reports/dr/backup-restore-drill-20260214T194129Z.json` (`before=after=1/1/1`).
- [x] Security gate (`bandit`, `pip-audit`, authz tests) passes.
  Evidence: `docs/reports/security/security-gate-20260214T194129Z.json` (`status=passed`).
- [x] Alert routes and on-call escalations are configured and tested.
  References: `docs/runbooks/oncall-operations.md`, `docs/runbooks/incident-response.md`.
