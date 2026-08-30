"""Fail-closed startup contract for the container entrypoint."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh"
BOOLEAN_VARIABLES = ("PREFLIGHT_ON_START", "PREFLIGHT_STRICT", "MIGRATE_ON_START")


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\nset -eu\n{body}", encoding="utf-8")
    path.chmod(0o755)


def _run_entrypoint(
    tmp_path: Path,
    *,
    values: dict[str, str] | None = None,
    preflight_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    event_log = tmp_path / "events.log"
    event_log.touch()

    _write_executable(
        bin_dir / "python",
        'printf "preflight:%s\\n" "$*" >> "$ENTRYPOINT_TEST_LOG"\n'
        'exit "$FAKE_PREFLIGHT_EXIT"\n',
    )
    _write_executable(
        bin_dir / "alembic",
        'printf "migration:%s\\n" "$*" >> "$ENTRYPOINT_TEST_LOG"\n',
    )
    _write_executable(
        bin_dir / "fake-app",
        'printf "app:%s\\n" "$*" >> "$ENTRYPOINT_TEST_LOG"\n',
    )

    env = os.environ.copy()
    for variable in BOOLEAN_VARIABLES:
        env.pop(variable, None)
    env.update(values or {})
    env.update(
        {
            "ENTRYPOINT_TEST_LOG": str(event_log),
            "FAKE_PREFLIGHT_EXIT": str(preflight_exit),
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["/bin/sh", str(ENTRYPOINT), "fake-app", "--serve"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )
    return result, event_log.read_text(encoding="utf-8").splitlines()


def test_missing_flags_use_safe_defaults(tmp_path: Path) -> None:
    result, events = _run_entrypoint(tmp_path, preflight_exit=9)

    assert result.returncode == 0
    assert events == ["app:--serve"]


@pytest.mark.parametrize("value", ["TRUE", "  true\t", "1", "YeS", " On "])
def test_truthy_spellings_enable_preflight_and_migration(tmp_path: Path, value: str) -> None:
    result, events = _run_entrypoint(
        tmp_path,
        values={
            "PREFLIGHT_ON_START": value,
            "PREFLIGHT_STRICT": value,
            "MIGRATE_ON_START": value,
        },
    )

    assert result.returncode == 0
    assert events == [
        "preflight:-m src.cli.production_preflight",
        "migration:upgrade head",
        "app:--serve",
    ]


@pytest.mark.parametrize("value", ["FALSE", "\tfalse  ", "0", "No", " oFf "])
def test_falsy_spellings_keep_preflight_and_migration_disabled(
    tmp_path: Path, value: str
) -> None:
    result, events = _run_entrypoint(
        tmp_path,
        values={
            "PREFLIGHT_ON_START": value,
            "PREFLIGHT_STRICT": value,
            "MIGRATE_ON_START": value,
        },
        preflight_exit=9,
    )

    assert result.returncode == 0
    assert events == ["app:--serve"]


def test_failed_preflight_stops_before_migration_or_app_by_default(tmp_path: Path) -> None:
    result, events = _run_entrypoint(
        tmp_path,
        values={"PREFLIGHT_ON_START": "true", "MIGRATE_ON_START": "true"},
        preflight_exit=9,
    )

    assert result.returncode == 1
    assert events == ["preflight:-m src.cli.production_preflight"]
    assert "preflight checks failed; refusing to start" in result.stderr


def test_non_strict_failed_preflight_may_continue(tmp_path: Path) -> None:
    result, events = _run_entrypoint(
        tmp_path,
        values={
            "PREFLIGHT_ON_START": " \tTrUe ",
            "PREFLIGHT_STRICT": "  FaLsE\t",
            "MIGRATE_ON_START": " 1 ",
        },
        preflight_exit=9,
    )

    assert result.returncode == 0
    assert events == [
        "preflight:-m src.cli.production_preflight",
        "migration:upgrade head",
        "app:--serve",
    ]
    assert "continuing because PREFLIGHT_STRICT=false" in result.stderr


@pytest.mark.parametrize("variable", BOOLEAN_VARIABLES)
@pytest.mark.parametrize("value", ["", "sometimes"])
def test_explicit_empty_or_unknown_value_fails_before_side_effects(
    tmp_path: Path, variable: str, value: str
) -> None:
    values = {
        "PREFLIGHT_ON_START": "true",
        "PREFLIGHT_STRICT": "false",
        "MIGRATE_ON_START": "true",
    }
    values[variable] = value

    result, events = _run_entrypoint(tmp_path, values=values)

    assert result.returncode == 2
    assert events == []
    assert f"{variable} must be one of:" in result.stderr
