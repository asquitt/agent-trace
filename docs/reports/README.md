# Reports Directory

> Status: Generated reports are synthetic repository-validation evidence. They do not prove
> deployment, production operation, provider integration, persistence, or customer outcomes.

`docs/reports` stores manually generated quality-gate outputs:

- `security/` from `./scripts/security_gate.sh`
- `dr/` from `./scripts/backup_restore_drill.sh`
- `perf/` from `./scripts/run_perf_gate.sh`

Report artifacts are intentionally git-ignored because they are timestamped,
environment-specific outputs. A manually dispatched workflow may retain them with immutable
repository, source-SHA, workflow, and run metadata. That retention does not change their
non-production evidence scope.
