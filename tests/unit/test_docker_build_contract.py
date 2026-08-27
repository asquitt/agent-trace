"""Static contracts for hermetic Docker builds and bootstrap health checks."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
HASHED_PIN = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)"
    r"(?:\s+--hash=sha256:[0-9a-f]{64})+"
)


def _canonical_package(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _hashed_lock_packages(path: Path) -> dict[str, str]:
    logical_requirements: list[str] = []
    buffer = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            assert not buffer
            continue
        if line.endswith("\\"):
            buffer += line[:-1].rstrip() + " "
            continue
        buffer += line
        logical_requirements.append(buffer)
        buffer = ""
    assert not buffer

    packages: dict[str, str] = {}
    for requirement in logical_requirements:
        match = HASHED_PIN.fullmatch(requirement)
        assert match is not None, f"unhashed or inexact lock entry: {requirement}"
        package, version = match.groups()
        canonical_package = _canonical_package(package)
        assert canonical_package not in packages
        packages[canonical_package] = version
    assert packages
    return packages


def test_pep517_backend_and_transitives_are_hash_locked_and_audited() -> None:
    pyproject = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    build_requirements = pyproject["build-system"]["requires"]
    direct_build_packages = {
        _canonical_package(re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", item).group())
        for item in build_requirements
    }
    build_input = {
        _canonical_package(match.group(1)): match.group(2)
        for line in (ROOT_DIR / "requirements/build.in").read_text(encoding="utf-8").splitlines()
        if (match := re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)", line.strip()))
    }
    build_lock = _hashed_lock_packages(ROOT_DIR / "requirements/build.lock")
    security_gate = (ROOT_DIR / "scripts/security_gate.sh").read_text(encoding="utf-8")

    assert direct_build_packages == set(build_input)
    assert direct_build_packages <= set(build_lock)
    assert '--requirement "$BUILD_LOCK_FILE"' in security_gate
    assert "--require-hashes" in security_gate
    assert "--disable-pip" in security_gate


def test_docker_builds_the_wheel_without_pep517_build_isolation() -> None:
    dockerfile = (ROOT_DIR / "docker/Dockerfile").read_text(encoding="utf-8")
    normalized = " ".join(dockerfile.split())

    assert dockerfile.count("FROM ${PYTHON_IMAGE}") == 2
    assert "requirements/build.lock" in dockerfile
    assert (
        "pip install --no-deps --require-hashes --requirement requirements/build.lock"
        in normalized
    )
    assert "pip wheel --no-deps --no-build-isolation --wheel-dir /wheelhouse ." in normalized
    assert "pip install --no-deps ." not in normalized
    assert "COPY --from=builder /wheelhouse/ /wheelhouse/" in dockerfile
    assert "pip install --no-deps /wheelhouse/*.whl" in normalized
    assert "pip check" in normalized


def test_docker_and_compose_use_the_same_dependency_free_healthcheck() -> None:
    dockerfile = (ROOT_DIR / "docker/Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT_DIR / "docker/docker-compose.yml").read_text(encoding="utf-8")
    healthcheck = (
        "urllib.request.urlopen('http://localhost:8000/health/live', timeout=5).close()"
    )

    assert healthcheck in dockerfile
    assert healthcheck in compose
    assert "curl" not in dockerfile.lower()
    assert "curl" not in compose.lower()
    assert '["CMD", "python", "-c"' in compose
