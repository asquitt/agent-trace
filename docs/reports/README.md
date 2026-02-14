# Reports Directory

`docs/reports` stores locally generated production-gate evidence:

- `security/` from `./scripts/security_gate.sh`
- `dr/` from `./scripts/backup_restore_drill.sh`
- `perf/` from `./scripts/run_perf_gate.sh`

Report artifacts are intentionally git-ignored because they are timestamped, environment-specific outputs. Persist them via CI artifacts in `.github/workflows/production-gates.yml` and your deployment evidence archive.
