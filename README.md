# AI Trace Internal Components

AI Trace is an internal inventory of telemetry and incident-evidence components. It is not
a standalone company, hosted product, or supported observability platform. Standalone product
and generic platform investment is frozen.

The repository is retained so active products can evaluate and selectively integrate useful
implementation patterns. Integration must happen in the product that owns the producer,
persistence, operator workflow, and customer outcome. Do not deploy or publish this repository
as a product.

## Retained Inventory

- Trace context, lifecycle, span, and decorator primitives under `src/tracing/`
- PostgreSQL trace persistence and trace-query contracts
- Structured trace, span, token, cost, error, and correlation evidence
- Incident patterns including anomaly deduplication, audit records, idempotent notification
  outbox delivery, and durable operation records
- Focused unit and integration tests that document local behavior

Provider wrappers, anomaly and policy engines, runtime controls, scheduler behavior, browser
sessions, the operator console, SIEM export, deployment templates, and production-preflight
machinery remain implementation inventory only. They are not active product commitments.

See [`docs/DISPOSITION.md`](docs/DISPOSITION.md) for the binding disposition, safe default
boundary, adoption gate, and known blockers.

## Evidence Boundary

### Implemented and locally testable

The repository contains source code, migrations, deterministic tests, local gate scripts, a
development console, and deployment examples. These can establish implementation behavior in a
controlled local environment when the relevant checks are run against an exact commit.

Historical test counts, generated reports, HTTP responses, built images, and checked-in manifests
are evidence about those specific checks. They are not standing proof for the current checkout.

### Not established

This repository does not establish:

- A hosted or production deployment
- A current public application identity or operator journey
- Delivery to, or enforcement by, a real product runtime
- Current provider compatibility, pricing accuracy, or lifecycle coverage
- Durable customer telemetry, incident response, or alert-receiver outcomes
- Customer adoption, reliability, unit economics, or standalone product demand

Any product making one of these claims must verify it in that product's own environment and retain
the exact revision, configuration, producer, persistence, consumer, and user-visible evidence.

## Safe Use

The default is read-only evaluation. An active product may adopt the smallest relevant component
only after defining its telemetry schema, sensitive-data policy, retention, failure semantics,
ownership, and acceptance tests. The first adoption belongs in the product's canonical layer; a
shared package is not justified until the second-consumer gate in `docs/DISPOSITION.md` passes.

The runtime-governance setting is a cold fail-closed boundary, not a distributed emergency stop:
it cannot revoke a control already delivered to an external runtime. Runtime-control adoption
requires the separate pre-execution revocation contract documented in `docs/DISPOSITION.md`.

The package metadata is private and is not intended for publication. Existing dependencies and
build requirements are retained because local repository gates still consume them; that retention
does not make the package API stable or supported.

## Local Verification

Run only the checks required for the component being evaluated. Typical repository checks are:

```bash
ruff check --select F src tests
pyright
pytest -q
```

Database, Docker, provider, browser, notification, performance, and disaster-recovery checks each
require their own explicit environment and prove only the boundary they actually exercise.

## Investment Policy

There is no standalone roadmap. Do not extend the generic console, control plane, deployment
topology, scheduler, policy engine, or observability platform without a concrete active-product
need and the adoption evidence required by `docs/DISPOSITION.md`.
