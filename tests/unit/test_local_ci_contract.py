"""Contracts for repository-owned local commit and push enforcement."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]


def test_hosted_pr_ci_is_replaced_without_disabling_production_drills() -> None:
    assert not (ROOT_DIR / ".github" / "workflows" / "ci.yml").exists()
    assert (ROOT_DIR / ".github" / "workflows" / "production-gates.yml").is_file()


def test_pre_commit_configuration_installs_the_fast_commit_hook() -> None:
    config = (ROOT_DIR / ".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "default_install_hook_types: [pre-commit]" in config
    assert "entry: bash scripts/local_ci.sh --commit" in config
    assert "stages: [pre-commit]" in config


def test_native_push_hook_validates_every_non_delete_ref_by_sha() -> None:
    hook = (ROOT_DIR / ".githooks" / "pre-push").read_text(encoding="utf-8")
    installer = (ROOT_DIR / "scripts" / "install_git_hooks.sh").read_text(
        encoding="utf-8"
    )

    assert "while read -r local_ref local_sha remote_ref remote_sha" in hook
    assert 'if [[ "$local_sha" == "$ZERO_SHA" ]]' in hook
    assert 'bash scripts/local_ci.sh --push "$local_sha"' in hook
    assert 'install -m 0755 "$ROOT_DIR/.githooks/pre-push"' in installer


def test_pre_commit_version_supports_the_repository_git_toolchain() -> None:
    pyproject = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]

    assert "pre-commit>=3.6.0,<4.0.0" in dev_dependencies


def test_push_gate_preserves_hosted_ci_coverage_and_console_quality() -> None:
    script = (ROOT_DIR / "scripts" / "local_ci.sh").read_text(encoding="utf-8")

    required_commands = (
        "ruff check --select F src tests",
        "pyright",
        "npm --prefix web test",
        "npm --prefix web run typecheck",
        "npm --prefix web run build",
        "./scripts/run_full_e2e.sh",
        "./scripts/security_gate.sh",
        "docker build -f docker/Dockerfile .",
    )
    for command in required_commands:
        assert command in script

    assert "SKIP" not in script
    assert 'git clone --quiet --no-hardlinks --no-checkout "$ROOT_DIR"' in script
    assert 'checkout --quiet --detach "$TARGET_SHA"' in script
    assert 'if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]' in script


def test_local_ci_rejects_unknown_modes() -> None:
    result = subprocess.run(
        ["bash", "scripts/local_ci.sh", "--unknown"],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_commit_mode_stops_before_push_only_gates() -> None:
    result = subprocess.run(
        ["bash", "scripts/local_ci.sh", "--commit"],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "mode: commit" in result.stdout
    assert "tool available: pyright" not in result.stdout


def test_push_tree_rejects_a_sha_other_than_the_checked_out_commit() -> None:
    wrong_sha = "0" * 40
    result = subprocess.run(
        ["bash", "scripts/local_ci.sh", "--push-tree", wrong_sha],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 1
    assert "exact-tree mismatch" in result.stderr
