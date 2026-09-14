# Local evaluation and maintenance contracts

## Safe Use

The default is read-only evaluation. An active product may adopt the smallest relevant component
only after defining its telemetry schema, sensitive-data policy, retention, failure semantics,
ownership, and acceptance tests. The first adoption belongs in the product's canonical layer; a
shared package is not justified until the second-consumer gate in `docs/DISPOSITION.md` passes.

The runtime-governance setting is a cold fail-closed boundary, not a distributed emergency stop:
it cannot revoke a control already delivered to an external runtime. Runtime-control adoption
requires the separate pre-execution revocation contract documented in `docs/DISPOSITION.md`.

`PROVIDER_EXECUTION_ENABLED=false` prevents the traced OpenAI and Anthropic wrappers from
constructing SDK clients or issuing requests. `TRACE_CAPTURE_PROMPTS=false` omits full prompt,
response, plaintext preview, raw exception text, and tracebacks from new traced provider calls;
failures retain deterministic safe error codes and exception types, and the request boundary returns
a generic 500 without re-logging the original exception. Capture-disabled reasoning persistence
retains structural and numeric fields while redacting model-derived descriptions, contexts, results,
and explanations. Tenant-scoped detail, reasoning, and export requests apply the caller's organization
predicate before prompt-bearing spans are loaded. Prompt, response, sensitive error, and
model-derived reasoning details require an administrator and explicit `include_prompts=true`. These
controls do not prove that historical stores contain no sensitive data; an adopting product must
audit and redact its own persisted records under its retention policy.

Rollback across the provider-execution gate is security-sensitive. Do not revert the gate while
provider credentials or provider-network egress remain available. Before rollback, remove or revoke
provider credentials, block provider egress, stop or drain existing processes and in-flight calls,
then verify that no provider request occurs under the rollback candidate.
`PROVIDER_EXECUTION_ENABLED=false` is insufficient for an older revision that does not implement
the gate.

The container entrypoint defaults `MIGRATE_ON_START=false`, rejects ambiguous Boolean flag values,
and performs no migration unless explicitly enabled. The local Compose reference opts its isolated
database into migrations; that does not authorize mutation of a product or shared database.

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
topology, scheduler, policy engine, or observability platform in this repository. A named active
product implements the smallest required capability in its own canonical layer; shared extraction
remains prohibited until the second-consumer gate in `docs/DISPOSITION.md` passes.
