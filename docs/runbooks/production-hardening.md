# Production Hardening Runbook

Last updated: August 27, 2026

> Status: Frozen manual reference for a future host-owned integration. The topology below is
> design material, not a current deployment, production requirement, or runtime evidence.

## Scope

This runbook records a possible host-owned hardening profile for deployment topology,
resilience, and monitoring.

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

## Historical Local Validation

August 27 local runs were previously reported for preflight, synthetic performance,
backup/restore, and security gates. Their ignored artifacts are not retained in this exact tree,
so they are historical observations rather than current or independently reproducible evidence.

Any future host-owned adoption must rerun the relevant gates against its immutable candidate and
target configuration, then separately prove deployment identity, persistence, alert routing,
rollback, and operator-visible behavior.
