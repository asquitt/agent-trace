"""Contracts for repository-owned local commit and push enforcement."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]


def test_all_hosted_workflows_require_manual_dispatch() -> None:
    assert not (ROOT_DIR / ".github" / "workflows" / "ci.yml").exists()
    workflows = list((ROOT_DIR / ".github" / "workflows").glob("*.y*ml"))
    assert workflows

    for path in workflows:
        header = path.read_text(encoding="utf-8").split("\njobs:", 1)[0]
        assert re.search(r"^  workflow_dispatch:\s*$", header, re.MULTILINE)
        for trigger in (
            "schedule",
            "push",
            "pull_request",
            "pull_request_target",
            "workflow_run",
            "workflow_call",
            "repository_dispatch",
            "release",
            "deployment",
        ):
            assert not re.search(rf"^  {trigger}:\s*$", header, re.MULTILINE)


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


def test_native_push_hook_reenters_arm64_when_git_runs_under_rosetta() -> None:
    hook = (ROOT_DIR / ".githooks" / "pre-push").read_text(encoding="utf-8")

    assert "sysctl.proc_translated" in hook
    assert "LOCAL_CI_NATIVE_REEXEC" in hook
    assert 'exec /usr/bin/arch -arm64 /bin/bash "$0" "$@"' in hook


def test_native_push_hook_processes_multiple_refs_and_ignores_deletes(
    tmp_path: Path,
) -> None:
    fake_repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    fake_scripts = fake_repo / "scripts"
    fake_bin.mkdir()
    fake_scripts.mkdir(parents=True)

    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1 $2" == "rev-parse --show-toplevel" ]]; then\n'
        '  printf "%s\\n" "$FAKE_REPO_ROOT"\n'
        "else\n"
        "  exit 2\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    call_log = tmp_path / "calls.txt"
    fake_local_ci = fake_scripts / "local_ci.sh"
    fake_local_ci.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$2" >> "$CALL_LOG"\n',
        encoding="utf-8",
    )
    fake_local_ci.chmod(0o755)

    first_sha = "1" * 40
    second_sha = "2" * 40
    zero_sha = "0" * 40
    push_input = (
        f"refs/heads/one {first_sha} refs/heads/one {zero_sha}\n"
        f"refs/heads/deleted {zero_sha} refs/heads/deleted {first_sha}\n"
        f"refs/heads/two {second_sha} refs/heads/two {first_sha}\n"
    )
    env = os.environ.copy()
    env.update(
        {
            "CALL_LOG": str(call_log),
            "FAKE_REPO_ROOT": str(fake_repo),
            "PATH": f"{fake_bin}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["/bin/bash", str(ROOT_DIR / ".githooks" / "pre-push"), "origin", "unused"],
        cwd=ROOT_DIR,
        check=False,
        capture_output=True,
        text=True,
        input=push_input,
        env=env,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert call_log.read_text(encoding="utf-8").splitlines() == [first_sha, second_sha]
    assert "deletion does not require local CI" in result.stdout


def test_pre_commit_version_supports_the_repository_git_toolchain() -> None:
    pyproject = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies = pyproject["project"]["optional-dependencies"]["dev"]

    assert "pre-commit>=3.6.0,<4.0.0" in dev_dependencies


def test_push_gate_preserves_repository_coverage_and_console_quality() -> None:
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
    assert 'export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"' in script
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
