"""Offline contract tests for the fail-closed dependency security gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
SECURITY_GATE = ROOT_DIR / "scripts" / "security_gate.sh"


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _run_gate(
    tmp_path: Path,
    *,
    audit_payload: str,
    audit_exit: int,
    waivers: dict[str, Any] | None = None,
    bandit_exit: int = 0,
    pytest_exit: int = 0,
    lock_contents: str | None = None,
    build_lock_contents: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any], str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    audit_fixture = tmp_path / "pip-audit.json"
    audit_fixture.write_text(audit_payload, encoding="utf-8")
    audit_args = tmp_path / "pip-audit-args.txt"
    pytest_args = tmp_path / "pytest-args.txt"
    report_dir = tmp_path / "reports"
    production_lock = tmp_path / "production.lock"
    production_lock.write_text(
        lock_contents or _locked_requirement(),
        encoding="utf-8",
    )
    build_lock = tmp_path / "build.lock"
    build_lock.write_text(
        build_lock_contents or lock_contents or _locked_requirement(),
        encoding="utf-8",
    )
    waiver_file = tmp_path / "waivers.json"
    waiver_file.write_text(
        json.dumps(waivers or {"version": 1, "waivers": []}),
        encoding="utf-8",
    )

    _write_executable(
        fake_bin / "bandit",
        f"#!/usr/bin/env bash\nexit {bandit_exit}\n",
    )
    _write_executable(
        fake_bin / "pytest",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$@" > "$FAKE_PYTEST_ARGS_FILE"
exit {pytest_exit}
""",
    )
    _write_executable(
        fake_bin / "pip-audit",
        """#!/usr/bin/env bash
set -u
printf '%s\\n' "$@" > "$FAKE_PIP_AUDIT_ARGS_FILE"
cat "$FAKE_PIP_AUDIT_JSON"
exit "$FAKE_PIP_AUDIT_EXIT"
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "PYTHON_BIN": sys.executable,
            "SECURITY_REPORT_DIR": str(report_dir),
            "SECURITY_VULNERABILITY_WAIVERS_FILE": str(waiver_file),
            "SECURITY_PRODUCTION_LOCK_FILE": str(production_lock),
            "SECURITY_BUILD_LOCK_FILE": str(build_lock),
            "FAKE_PIP_AUDIT_JSON": str(audit_fixture),
            "FAKE_PIP_AUDIT_EXIT": str(audit_exit),
            "FAKE_PIP_AUDIT_ARGS_FILE": str(audit_args),
            "FAKE_PYTEST_ARGS_FILE": str(pytest_args),
        }
    )
    completed = subprocess.run(
        ["bash", str(SECURITY_GATE)],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    reports = list(report_dir.glob("security-gate-*.json"))
    assert len(reports) == 1, completed.stdout + completed.stderr
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    return (
        completed,
        report,
        audit_args.read_text(encoding="utf-8"),
        pytest_args.read_text(encoding="utf-8"),
    )


def _locked_requirement(
    package: str = "example-runtime",
    version: str = "1.0.0",
) -> str:
    return f"{package}=={version} \\\n    --hash=sha256:{'0' * 64}\n"


def _audit_payload(*vulns: dict[str, Any]) -> str:
    return json.dumps(
        {
            "dependencies": [
                {
                    "name": "example-runtime",
                    "version": "1.0.0",
                    "vulns": list(vulns),
                }
            ],
            "fixes": [],
        }
    )


def test_gate_passes_only_clean_production_dependency_audit(tmp_path: Path) -> None:
    completed, report, audit_args, pytest_args = _run_gate(
        tmp_path,
        audit_payload=_audit_payload(),
        audit_exit=0,
    )

    assert completed.returncode == 0
    assert report["status"] == "passed"
    assert report["pip_audit"]["evaluation"]["total_vulnerabilities"] == 0
    assert "--strict\n" in audit_args
    assert "--format\njson\n" in audit_args
    assert "--require-hashes\n" in audit_args
    assert "--disable-pip\n" in audit_args
    assert audit_args.count("--requirement\n") == 2
    assert pytest_args.splitlines() == [
        "-q",
        "tests/unit/test_security.py",
        "tests/unit/test_api_metrics.py",
        "tests/unit/test_observability_api_security.py",
        "tests/unit/test_docker_build_contract.py",
    ]
    assert report["policy"]["production_lock_sha256"]
    assert report["policy"]["build_lock_sha256"]


def test_gate_fails_for_any_unwaived_vulnerability(tmp_path: Path) -> None:
    completed, report, _, _ = _run_gate(
        tmp_path,
        audit_payload=_audit_payload(
            {
                "id": "CVE-2099-0001",
                "aliases": ["GHSA-example"],
                "fix_versions": ["1.0.1"],
            }
        ),
        audit_exit=1,
    )

    evaluation = report["pip_audit"]["evaluation"]
    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert evaluation["total_vulnerabilities"] == 1
    assert evaluation["unwaived_vulnerabilities"] == 1
    assert evaluation["unwaived"][0]["vulnerability_id"] == "CVE-2099-0001"


def test_gate_fails_closed_when_pip_audit_output_cannot_be_parsed(tmp_path: Path) -> None:
    completed, report, _, _ = _run_gate(
        tmp_path,
        audit_payload="not-json\n",
        audit_exit=2,
    )

    evaluation = report["pip_audit"]["evaluation"]
    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert evaluation["status"] == "error"
    assert "did not produce valid JSON" in evaluation["reason"]


def test_gate_fails_closed_for_an_empty_dependency_graph(tmp_path: Path) -> None:
    completed, report, _, _ = _run_gate(
        tmp_path,
        audit_payload=json.dumps({"dependencies": [], "fixes": []}),
        audit_exit=0,
    )

    evaluation = report["pip_audit"]["evaluation"]
    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert evaluation["status"] == "error"
    assert "empty production dependency graph" in evaluation["reason"]


def test_gate_accepts_only_an_active_explicit_waiver(tmp_path: Path) -> None:
    completed, report, _, _ = _run_gate(
        tmp_path,
        audit_payload=_audit_payload(
            {
                "id": "CVE-2099-0001",
                "aliases": [],
                "fix_versions": [],
            }
        ),
        audit_exit=1,
        waivers={
            "version": 1,
            "waivers": [
                {
                    "id": "WAIVER-001",
                    "package": "example-runtime",
                    "vulnerability_id": "CVE-2099-0001",
                    "owner": "security@example.com",
                    "reason": "No fixed release exists; compensating control is documented.",
                    "expires_on": "2999-12-31",
                }
            ],
        },
    )

    evaluation = report["pip_audit"]["evaluation"]
    assert completed.returncode == 0
    assert report["status"] == "passed"
    assert evaluation["waived_vulnerabilities"] == 1
    assert evaluation["applied_waiver_ids"] == ["WAIVER-001"]


def test_gate_rejects_an_expired_waiver(tmp_path: Path) -> None:
    completed, report, _, _ = _run_gate(
        tmp_path,
        audit_payload=_audit_payload(
            {
                "id": "CVE-2099-0001",
                "aliases": [],
                "fix_versions": [],
            }
        ),
        audit_exit=1,
        waivers={
            "version": 1,
            "waivers": [
                {
                    "id": "WAIVER-OLD",
                    "package": "example-runtime",
                    "vulnerability_id": "CVE-2099-0001",
                    "owner": "security@example.com",
                    "reason": "Historical waiver that must not remain effective.",
                    "expires_on": "2000-01-01",
                }
            ],
        },
    )

    evaluation = report["pip_audit"]["evaluation"]
    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert evaluation["status"] == "error"
    assert "expired" in evaluation["reason"]


def test_gate_rejects_audit_graph_that_differs_from_the_docker_lock(tmp_path: Path) -> None:
    completed, report, _, _ = _run_gate(
        tmp_path,
        audit_payload=_audit_payload(),
        audit_exit=0,
        lock_contents=_locked_requirement(version="2.0.0"),
    )

    evaluation = report["pip_audit"]["evaluation"]
    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert evaluation["status"] == "error"
    assert "does not exactly match" in evaluation["reason"]
    assert "version_mismatches=['example-runtime']" in evaluation["reason"]


def test_gate_rejects_an_unhashed_production_lock(tmp_path: Path) -> None:
    completed, report, _, _ = _run_gate(
        tmp_path,
        audit_payload=_audit_payload(),
        audit_exit=0,
        lock_contents="example-runtime==1.0.0\n",
    )

    evaluation = report["pip_audit"]["evaluation"]
    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert evaluation["status"] == "error"
    assert "not an exact hash-checked pin" in evaluation["reason"]


@pytest.mark.parametrize(
    ("bandit_exit", "pytest_exit"),
    [(1, 0), (0, 1)],
)
def test_gate_fails_when_another_security_check_fails(
    tmp_path: Path,
    bandit_exit: int,
    pytest_exit: int,
) -> None:
    completed, report, _, _ = _run_gate(
        tmp_path,
        audit_payload=_audit_payload(),
        audit_exit=0,
        bandit_exit=bandit_exit,
        pytest_exit=pytest_exit,
    )

    assert completed.returncode == 1
    assert report["status"] == "failed"
    assert report["bandit"]["exit_code"] == bandit_exit
    assert report["pytest_security"]["exit_code"] == pytest_exit


def test_gate_rejects_the_removed_vulnerability_threshold_override() -> None:
    env = os.environ.copy()
    env["SECURITY_MAX_KNOWN_VULNS"] = "50"

    completed = subprocess.run(
        ["bash", str(SECURITY_GATE)],
        cwd=ROOT_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "no longer supported" in completed.stderr
