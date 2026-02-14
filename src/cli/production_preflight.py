"""CLI entrypoint for production preflight checks."""

from __future__ import annotations

from ..config import Settings
from ..services.production_preflight import print_results, run_preflight


def main() -> int:
    settings = Settings()
    results = run_preflight(settings)
    print_results(results)
    return 1 if any(result.status == "fail" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
