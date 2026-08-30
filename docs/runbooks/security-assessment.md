# Security Assessment Runbook

Last updated: August 27, 2026

> Status: Frozen manual reference. Gates and reports cover repository artifacts only unless a
> host system separately records exact deployment, runtime, provider, and operator evidence.

## Objective

Provide repeatable, manually invoked security checks for an internal component candidate.

## Automated Security Gates

Run:

```bash
./scripts/security_gate.sh
```

The gate runs all of the following and fails if any result cannot be collected or
validated:

1. `bandit` against `src` and `scripts`.
2. `pip-audit --strict` against the exact, hash-checked runtime and build graphs in
   `requirements/production.lock` and `requirements/build.lock`, without resolving a new
   graph. Docker consumes these same artifacts. Development extras and unrelated packages
   in the invoking Python environment are not counted as Docker findings.
3. The authentication, browser-session/CSRF, same-origin console, tenant-isolation,
   global-metrics, and Docker security contracts in `tests/unit/test_security.py`,
   `tests/unit/test_browser_sessions.py`, `tests/unit/test_console_router.py`,
   `tests/unit/test_api_metrics.py`, `tests/unit/test_observability_api_security.py`,
   and `tests/unit/test_docker_build_contract.py`.

The dependency audit is evaluated from structured JSON. A command failure, resolution
failure, missing hash, lock/audit graph mismatch, skipped dependency, malformed output,
inconsistent exit code, or unwaived known vulnerability fails the release gate. The
report records the audited lock's SHA-256. There is no vulnerability-count allowance.

## Maintaining the Production Lock

The lock targets the digest-pinned Python 3.11.15 Linux image in `docker/Dockerfile`.
Regenerate it only as an intentional dependency update and review the complete diff:

```bash
uv pip compile pyproject.toml \
  --python-version 3.11.15 \
  --python-platform linux \
  --generate-hashes \
  --no-annotate \
  --upgrade \
  --output-file requirements/production.lock
uv pip compile requirements/build.in \
  --python-version 3.11.15 \
  --python-platform linux \
  --generate-hashes \
  --no-annotate \
  --upgrade \
  --output-file requirements/build.lock
./scripts/security_gate.sh
docker build -f docker/Dockerfile .
```

The Docker builder installs the exact build lock and creates the application wheel with
`--no-build-isolation --no-deps`. The runtime stage installs the exact production lock,
installs only that wheel, and runs `pip check`. Editable local development remains
`pip install -e ".[dev]"` and is intentionally separate from the Docker artifacts.

## Vulnerability Waivers

The default waiver policy is `docs/security/vulnerability-waivers.json`. A waiver must
match an exact package and vulnerability ID (or a reported alias) and include all of:

```json
{
  "id": "WAIVER-2026-001",
  "package": "example-package",
  "vulnerability_id": "CVE-2099-0001",
  "owner": "security@example.com",
  "reason": "Compensating control and remediation plan.",
  "expires_on": "2026-09-01"
}
```

The expiry is an ISO `YYYY-MM-DD` date. Missing, malformed, duplicate, or expired
waivers fail the gate. Waivers are temporary risk acceptances: review them as security
changes and remove them when the package is remediated.

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

- No findings from automated source gates.
- Zero unwaived known vulnerabilities in the exact hash-locked runtime and build graphs.
- Every retained vulnerability waiver has an accountable owner, documented reason,
  and future expiry date.
- Authz and tenant-scoping tests pass.
- DR and perf gates pass for release candidate.
