#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${SECURITY_MAX_KNOWN_VULNS+x}" ]]; then
  echo "[security] SECURITY_MAX_KNOWN_VULNS is no longer supported; the gate allows zero unwaived vulnerabilities" >&2
  exit 2
fi

REPORT_DIR="${SECURITY_REPORT_DIR:-docs/reports/security}"
WAIVER_FILE="${SECURITY_VULNERABILITY_WAIVERS_FILE:-docs/security/vulnerability-waivers.json}"
PRODUCTION_LOCK_FILE="${SECURITY_PRODUCTION_LOCK_FILE:-requirements/production.lock}"
BUILD_LOCK_FILE="${SECURITY_BUILD_LOCK_FILE:-requirements/build.lock}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
mkdir -p "$REPORT_DIR"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"

BANDIT_OUT="${REPORT_DIR}/bandit-${TIMESTAMP}.txt"
PIP_AUDIT_OUT="${REPORT_DIR}/pip-audit-${TIMESTAMP}.json"
PIP_AUDIT_LOG="${REPORT_DIR}/pip-audit-${TIMESTAMP}.log"
PIP_AUDIT_EVALUATION="${REPORT_DIR}/pip-audit-evaluation-${TIMESTAMP}.json"
PYTEST_OUT="${REPORT_DIR}/security-tests-${TIMESTAMP}.txt"
REPORT_JSON="${REPORT_DIR}/security-gate-${TIMESTAMP}.json"

echo "[security] running bandit"
set +e
bandit -q -r src scripts >"$BANDIT_OUT" 2>&1
BANDIT_EXIT=$?
set -e

echo "[security] auditing the exact locked production dependency graph"
set +e
pip-audit \
  --strict \
  --format json \
  --progress-spinner off \
  --requirement "$PRODUCTION_LOCK_FILE" \
  --requirement "$BUILD_LOCK_FILE" \
  --require-hashes \
  --disable-pip \
  >"$PIP_AUDIT_OUT" 2>"$PIP_AUDIT_LOG"
PIP_AUDIT_EXIT=$?
set -e

echo "[security] validating audit results and vulnerability waivers"
set +e
"$PYTHON_BIN" - \
  "$PIP_AUDIT_OUT" \
  "$WAIVER_FILE" \
  "$PRODUCTION_LOCK_FILE" \
  "$BUILD_LOCK_FILE" \
  "$PIP_AUDIT_EXIT" \
  "$PIP_AUDIT_EVALUATION" <<'PY'
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


audit_path = Path(sys.argv[1])
waiver_path = Path(sys.argv[2])
lock_path = Path(sys.argv[3])
build_lock_path = Path(sys.argv[4])
audit_exit = int(sys.argv[5])
evaluation_path = Path(sys.argv[6])


def write_evaluation(
    *,
    status: str,
    reason: str,
    dependencies_audited: int | None = None,
    total_vulnerabilities: int | None = None,
    waived_vulnerabilities: int | None = None,
    unwaived: list[dict[str, Any]] | None = None,
    applied_waiver_ids: list[str] | None = None,
) -> None:
    unwaived_items = unwaived or []
    payload = {
        "status": status,
        "reason": reason,
        "evaluation_valid": status != "error",
        "dependencies_audited": dependencies_audited,
        "total_vulnerabilities": total_vulnerabilities,
        "waived_vulnerabilities": waived_vulnerabilities,
        "unwaived_vulnerabilities": (
            len(unwaived_items) if total_vulnerabilities is not None else None
        ),
        "applied_waiver_ids": applied_waiver_ids or [],
        "unwaived": unwaived_items,
    }
    evaluation_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def fail_validation(reason: str) -> None:
    write_evaluation(status="error", reason=reason)
    raise SystemExit(1)


try:
    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    fail_validation(f"pip-audit did not produce valid JSON: {exc}")

if not isinstance(audit_payload, dict) or not isinstance(
    audit_payload.get("dependencies"), list
):
    fail_validation("pip-audit JSON must contain a dependencies list")
if not audit_payload["dependencies"]:
    fail_validation("pip-audit returned an empty production dependency graph")

hashed_pin_pattern = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)"
    r"(?:\s+--hash=sha256:[0-9a-f]{64})+"
)


def parse_hashed_lock(path: Path, label: str) -> dict[str, tuple[str, str]]:
    try:
        lock_lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        fail_validation(f"{label} dependency lock is missing or unreadable: {exc}")

    logical_requirements: list[tuple[int, str]] = []
    requirement_buffer = ""
    requirement_start_line = 0
    for line_number, raw_line in enumerate(lock_lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if requirement_buffer:
                fail_validation(
                    f"{label} dependency lock has an interrupted requirement at line "
                    f"{requirement_start_line}"
                )
            continue
        if not requirement_buffer:
            requirement_start_line = line_number
        if line.endswith("\\"):
            requirement_buffer += line[:-1].rstrip() + " "
            continue
        requirement_buffer += line
        logical_requirements.append((requirement_start_line, requirement_buffer))
        requirement_buffer = ""

    if requirement_buffer:
        fail_validation(
            f"{label} dependency lock has an unterminated requirement at line "
            f"{requirement_start_line}"
        )

    dependencies: dict[str, tuple[str, str]] = {}
    for line_number, requirement in logical_requirements:
        match = hashed_pin_pattern.fullmatch(requirement)
        if match is None:
            fail_validation(
                f"{label} dependency lock line {line_number} is not an exact hash-checked pin"
            )
        package, version = match.groups()
        canonical_package = re.sub(r"[-_.]+", "-", package).lower()
        if canonical_package in dependencies:
            fail_validation(f"{label} dependency lock repeats package {package!r}")
        dependencies[canonical_package] = (package, version)

    if not dependencies:
        fail_validation(f"{label} dependency lock contains no exact package pins")
    return dependencies


locked_dependencies = parse_hashed_lock(lock_path, "production")
build_locked_dependencies = parse_hashed_lock(build_lock_path, "build")
for canonical_package, package_version in build_locked_dependencies.items():
    existing = locked_dependencies.get(canonical_package)
    if existing is not None and existing[1] != package_version[1]:
        fail_validation(
            f"production and build locks disagree on {canonical_package!r}: "
            f"{existing[1]!r} != {package_version[1]!r}"
        )
    locked_dependencies[canonical_package] = package_version

vulnerabilities: list[dict[str, Any]] = []
audited_dependencies: dict[str, tuple[str, str]] = {}
for dependency in audit_payload["dependencies"]:
    if not isinstance(dependency, dict):
        fail_validation("pip-audit dependencies must be JSON objects")
    package = dependency.get("name")
    version = dependency.get("version")
    dependency_vulns = dependency.get("vulns")
    if not isinstance(package, str) or not package.strip():
        fail_validation("pip-audit dependency is missing a package name")
    if not isinstance(version, str) or not version.strip():
        fail_validation(f"pip-audit dependency {package!r} is missing a version")
    if dependency.get("skip_reason"):
        fail_validation(
            f"pip-audit skipped dependency {package} {version}: {dependency['skip_reason']}"
        )
    if not isinstance(dependency_vulns, list):
        fail_validation(f"pip-audit dependency {package!r} is missing a vulns list")

    canonical_package = re.sub(r"[-_.]+", "-", package).lower()
    if canonical_package in audited_dependencies:
        fail_validation(f"pip-audit repeated dependency {package!r}")
    audited_dependencies[canonical_package] = (package, version)

    for vulnerability in dependency_vulns:
        if not isinstance(vulnerability, dict):
            fail_validation(f"pip-audit vulnerability for {package!r} is not an object")
        vulnerability_id = vulnerability.get("id")
        aliases = vulnerability.get("aliases", [])
        fix_versions = vulnerability.get("fix_versions", [])
        if not isinstance(vulnerability_id, str) or not vulnerability_id.strip():
            fail_validation(f"pip-audit vulnerability for {package!r} is missing an id")
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            fail_validation(f"pip-audit vulnerability {vulnerability_id!r} has invalid aliases")
        if not isinstance(fix_versions, list) or not all(
            isinstance(item, str) for item in fix_versions
        ):
            fail_validation(
                f"pip-audit vulnerability {vulnerability_id!r} has invalid fix_versions"
            )
        vulnerabilities.append(
            {
                "package": package,
                "version": version,
                "vulnerability_id": vulnerability_id,
                "aliases": aliases,
                "fix_versions": fix_versions,
            }
        )

if audited_dependencies != locked_dependencies:
    missing = sorted(set(locked_dependencies) - set(audited_dependencies))
    unexpected = sorted(set(audited_dependencies) - set(locked_dependencies))
    version_mismatches = sorted(
        canonical_package
        for canonical_package in set(locked_dependencies) & set(audited_dependencies)
        if locked_dependencies[canonical_package][1]
        != audited_dependencies[canonical_package][1]
    )
    fail_validation(
        "pip-audit result does not exactly match the Docker dependency locks "
        f"(missing={missing}, unexpected={unexpected}, version_mismatches={version_mismatches})"
    )

if audit_exit not in (0, 1):
    fail_validation(f"pip-audit failed with unexpected exit code {audit_exit}")
if vulnerabilities and audit_exit != 1:
    fail_validation("pip-audit reported vulnerabilities but returned a success exit code")
if not vulnerabilities and audit_exit != 0:
    fail_validation("pip-audit failed without reporting a vulnerability")

try:
    waiver_payload = json.loads(waiver_path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    fail_validation(f"vulnerability waiver file is missing or invalid: {exc}")

if not isinstance(waiver_payload, dict) or waiver_payload.get("version") != 1:
    fail_validation("vulnerability waiver file must be an object with version 1")
waivers = waiver_payload.get("waivers")
if not isinstance(waivers, list):
    fail_validation("vulnerability waiver file must contain a waivers list")

required_fields = (
    "id",
    "package",
    "vulnerability_id",
    "owner",
    "reason",
    "expires_on",
)
today = datetime.now(timezone.utc).date()
seen_waiver_ids: set[str] = set()
seen_targets: set[tuple[str, str]] = set()
active_waivers: list[dict[str, str]] = []
for waiver in waivers:
    if not isinstance(waiver, dict):
        fail_validation("each vulnerability waiver must be a JSON object")
    for field in required_fields:
        value = waiver.get(field)
        if not isinstance(value, str) or not value.strip():
            fail_validation(f"vulnerability waiver is missing non-empty field {field!r}")

    waiver_id = waiver["id"].strip()
    package = waiver["package"].strip().lower()
    vulnerability_id = waiver["vulnerability_id"].strip()
    if waiver_id in seen_waiver_ids:
        fail_validation(f"duplicate vulnerability waiver id {waiver_id!r}")
    target = (package, vulnerability_id)
    if target in seen_targets:
        fail_validation(
            f"duplicate vulnerability waiver target {package!r}/{vulnerability_id!r}"
        )
    seen_waiver_ids.add(waiver_id)
    seen_targets.add(target)

    try:
        expires_on = date.fromisoformat(waiver["expires_on"])
    except ValueError:
        fail_validation(
            f"vulnerability waiver {waiver_id!r} expires_on must use YYYY-MM-DD"
        )
    if waiver["expires_on"] != expires_on.isoformat():
        fail_validation(
            f"vulnerability waiver {waiver_id!r} expires_on must use YYYY-MM-DD"
        )
    if expires_on < today:
        fail_validation(
            f"vulnerability waiver {waiver_id!r} expired on {expires_on.isoformat()}"
        )

    active_waivers.append(
        {
            "id": waiver_id,
            "package": package,
            "vulnerability_id": vulnerability_id,
        }
    )

unwaived: list[dict[str, Any]] = []
applied_waiver_ids: set[str] = set()
for vulnerability in vulnerabilities:
    known_ids = {
        vulnerability["vulnerability_id"],
        *vulnerability["aliases"],
    }
    matching_waiver = next(
        (
            waiver
            for waiver in active_waivers
            if waiver["package"] == vulnerability["package"].lower()
            and waiver["vulnerability_id"] in known_ids
        ),
        None,
    )
    if matching_waiver is None:
        unwaived.append(
            {
                "package": vulnerability["package"],
                "version": vulnerability["version"],
                "vulnerability_id": vulnerability["vulnerability_id"],
                "fix_versions": vulnerability["fix_versions"],
            }
        )
    else:
        applied_waiver_ids.add(matching_waiver["id"])

dependency_count = len(audit_payload["dependencies"])
vulnerability_count = len(vulnerabilities)
waived_count = vulnerability_count - len(unwaived)
if unwaived:
    write_evaluation(
        status="failed",
        reason=f"{len(unwaived)} unwaived known vulnerabilities found",
        dependencies_audited=dependency_count,
        total_vulnerabilities=vulnerability_count,
        waived_vulnerabilities=waived_count,
        unwaived=unwaived,
        applied_waiver_ids=sorted(applied_waiver_ids),
    )
    raise SystemExit(1)

write_evaluation(
    status="passed",
    reason=(
        "no known vulnerabilities found"
        if not vulnerabilities
        else "all known vulnerabilities have active explicit waivers"
    ),
    dependencies_audited=dependency_count,
    total_vulnerabilities=vulnerability_count,
    waived_vulnerabilities=waived_count,
    unwaived=[],
    applied_waiver_ids=sorted(applied_waiver_ids),
)
PY
PIP_AUDIT_EVALUATION_EXIT=$?
set -e

echo "[security] running authz security tests"
set +e
pytest -q \
  tests/unit/test_security.py \
  tests/unit/test_api_metrics.py \
  tests/unit/test_observability_api_security.py \
  tests/unit/test_docker_build_contract.py \
  >"$PYTEST_OUT" 2>&1
PYTEST_EXIT=$?
set -e

STATUS="passed"
if [[ "$BANDIT_EXIT" -ne 0 || "$PIP_AUDIT_EVALUATION_EXIT" -ne 0 || "$PYTEST_EXIT" -ne 0 ]]; then
  STATUS="failed"
fi

"$PYTHON_BIN" - \
  "$REPORT_JSON" \
  "$STATUS" \
  "$TIMESTAMP" \
  "$WAIVER_FILE" \
  "$PRODUCTION_LOCK_FILE" \
  "$BUILD_LOCK_FILE" \
  "$BANDIT_EXIT" \
  "$PIP_AUDIT_EXIT" \
  "$PIP_AUDIT_EVALUATION_EXIT" \
  "$PYTEST_EXIT" \
  "$BANDIT_OUT" \
  "$PIP_AUDIT_OUT" \
  "$PIP_AUDIT_LOG" \
  "$PIP_AUDIT_EVALUATION" \
  "$PYTEST_OUT" <<'PY'
import json
import sys
from pathlib import Path

(
    report_path,
    status,
    timestamp,
    waiver_file,
    production_lock_file,
    build_lock_file,
    bandit_exit,
    pip_audit_exit,
    evaluation_exit,
    pytest_exit,
    bandit_artifact,
    pip_audit_artifact,
    pip_audit_log,
    evaluation_artifact,
    pytest_artifact,
) = sys.argv[1:]

production_lock_path = Path(production_lock_file)
build_lock_path = Path(build_lock_file)
try:
    import hashlib

    production_lock_sha256 = hashlib.sha256(production_lock_path.read_bytes()).hexdigest()
    build_lock_sha256 = hashlib.sha256(build_lock_path.read_bytes()).hexdigest()
except OSError:
    production_lock_sha256 = None
    build_lock_sha256 = None

try:
    evaluation = json.loads(Path(evaluation_artifact).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    evaluation = {
        "status": "error",
        "reason": f"could not read pip-audit evaluation artifact: {exc}",
        "evaluation_valid": False,
    }

report = {
    "timestamp_utc": timestamp,
    "status": status,
    "policy": {
        "dependency_scope": "exact Docker runtime and build dependency locks",
        "production_lock_file": production_lock_file,
        "production_lock_sha256": production_lock_sha256,
        "build_lock_file": build_lock_file,
        "build_lock_sha256": build_lock_sha256,
        "unwaived_vulnerabilities_allowed": 0,
        "waiver_file": waiver_file,
    },
    "bandit": {"exit_code": int(bandit_exit)},
    "pip_audit": {
        "exit_code": int(pip_audit_exit),
        "evaluation_exit_code": int(evaluation_exit),
        "evaluation": evaluation,
    },
    "pytest_security": {"exit_code": int(pytest_exit)},
    "artifacts": {
        "bandit": bandit_artifact,
        "pip_audit_json": pip_audit_artifact,
        "pip_audit_log": pip_audit_log,
        "pip_audit_evaluation": evaluation_artifact,
        "pytest_security": pytest_artifact,
    },
}
Path(report_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
PY

echo "[security] report written: ${REPORT_JSON}"
cat "$REPORT_JSON"

if [[ "$STATUS" != "passed" ]]; then
  echo "[security] failed; inspect the report and referenced artifacts" >&2
  exit 1
fi
