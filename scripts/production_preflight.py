#!/usr/bin/env python3
"""Production readiness preflight checks for AI Trace deployment settings."""

from __future__ import annotations

from src.config import Settings
from src.services.production_preflight import print_results, run_preflight


def main() -> int:
    settings = Settings()
    results = run_preflight(settings)
    print_results(results)
    return 1 if any(result.status == "fail" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
