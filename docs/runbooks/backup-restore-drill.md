# Backup/Restore Drill Runbook

Last updated: February 14, 2026

## Goal

Validate backup integrity and disaster recovery restore procedures for AI Trace PostgreSQL state.

## Automated Drill

Run:

```bash
./scripts/backup_restore_drill.sh
```

The drill performs:

1. Fresh isolated Postgres boot.
2. Full migration apply.
3. Seed representative deployment/session/action records.
4. Compressed `pg_dump` backup creation.
5. Simulated disaster (`DROP SCHEMA public CASCADE`).
6. Full restore using `pg_restore`.
7. Before/after row-count validation.
8. JSON report artifact generation.

## Artifacts

- Backup dumps: `docs/reports/dr/backup-<timestamp>.dump`
- Drill reports: `docs/reports/dr/backup-restore-drill-<timestamp>.json`

## Pass Criteria

- Drill exits with status 0.
- Report `status` is `true`.
- Before/after counts match for:
  - `agent_deployments`
  - `agent_sessions`
  - `agent_actions`

## Required Frequency

- Minimum weekly in staging.
- Minimum monthly in production.
- Mandatory before and after major schema changes.
