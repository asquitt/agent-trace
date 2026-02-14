# Security Assessment Runbook

Last updated: February 14, 2026

## Objective

Provide repeatable security gates for each release and quarterly external review readiness.

## Automated Security Gates

Run:

```bash
bandit -q -r src scripts
pip-audit
pytest -q tests/unit/test_security.py
```

Optional deep gate:

```bash
ruff check --select B904,B017 src tests
```

## External Assessment Package

For third-party pentest execution, provide:

1. API endpoint inventory (`README.md` API Surface).
2. Auth model and RBAC behavior (`src/security.py`).
3. Deployment hardening manifests (`deploy/k8s/*`).
4. Monitoring and incident response artifacts (`deploy/monitoring/*`, `docs/runbooks/*`).
5. Latest test/e2e/perf/DR reports (`docs/reports/**`).

## Signoff Criteria

- No critical/high findings from automated gates.
- Dependency vulnerabilities remediated or formally risk-accepted.
- Authz and tenant-scoping tests pass.
- DR and perf gates pass for release candidate.
