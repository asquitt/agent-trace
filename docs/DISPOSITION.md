# AI Trace Product Disposition

## Decision

AI Trace is not a standalone company or product. It is an internal implementation inventory for
telemetry and incident evidence. Standalone observability-platform investment is frozen, and
components are integrated only when an active product has a concrete need and owns the resulting
runtime and customer outcome.

This document governs repository positioning. It does not claim that deferred code has been
removed or that any runtime, deployment, provider, or customer integration exists.

## Evidence Posture

Source code, migrations, deterministic tests, local reports, HTTP probes, and built images can
prove bounded implementation properties for an exact revision and environment. They do not by
themselves prove deployment, provider execution, persistence under real load, alert delivery to a
human, operator response, customer value, or production reliability.

Claims must identify the real telemetry producer, persistence owner, policy or incident consumer,
operator-visible result, exact revision, configuration, and environment. Missing evidence remains
unverified rather than being inferred from local checks.

## Disposition Matrix

| Disposition | Inventory | Current handling | Re-entry condition |
|---|---|---|---|
| Retained | Trace context, tracer and span lifecycle, decorators, typed trace data, PostgreSQL trace storage, trace query contracts, correlation/error/token evidence, and focused tests | Preserve as internal source and behavior documentation. Maintain only when needed to keep an adopted component correct. | A named active product owns the use case, data contract, persistence, tests, and operator or customer outcome. |
| Retained | Incident-evidence patterns such as anomaly deduplication, audit records, durable operation records, and idempotent notification-outbox mechanics | Preserve as reference implementations. Adopt the smallest pattern rather than the complete platform. | A product incident journey requires the pattern and verifies producer-to-consumer behavior, retries, persistence, and operator truth. |
| Deferred | Provider wrappers, prompt/response capture, cost estimation, anomaly detectors, policy evaluation, runtime-control claim/acknowledgement, scheduler execution, notifications, SIEM export, and browser-session infrastructure | No generic feature work, extraction, compatibility promise, or deployment claim. | Product-owned requirements and acceptance tests exist; sensitive-data and failure contracts are resolved; second-consumer gate applies before shared extraction. |
| Frozen | Fleet observability/control-plane positioning, operator console expansion, standalone authentication administration, hosted service hardening, Kubernetes topology, SLOs, production preflight, and generic on-call operations | Preserve history but do not advance, promote, deploy, or use as evidence of an active service. | No standalone re-entry path. A product may independently implement a required capability inside its own architecture. |
| Removed from active investment | Standalone branding, customer acquisition, public package distribution, hosted-console roadmap, generic observability roadmap, provider-neutral runtime-governance roadmap, and standalone release/deployment work | Excluded from planning, prioritization, release claims, and success metrics. | None. A future product decision would require a new explicit owner decision, not continuation of this roadmap. |

## Safe Default Boundary

The safe default is read-only evaluation of source and tests. Bounded repository maintenance may
be explicitly authorized without a product adoption only for governance, dependency/security, or
risk-reducing corrections that add no capability, compatibility promise, deployment path, or
product-adoption behavior. Isolated proof for that maintenance does not authorize deployment,
provider execution, adoption, or capability expansion. Unless one of those maintenance corrections
or a product-local adoption is explicitly authorized:

- Do not publish the Python package or describe its interfaces as stable.
- Do not deploy the API, console, scheduler, runtime controls, or deployment templates.
- Do not run migrations against a product or shared database.
- Do not enable provider calls, prompt capture, outbound notifications, SIEM export, policy
  actions, throttle, or shutdown behavior.
- Do not treat local tests, generated reports, HTTP success, database rows, or manifests as
  production or customer proof.
- Do not create a new shared telemetry service, compatibility layer, or generic abstraction.

The first consumer implements the needed behavior in the product's canonical layer. Source may be
adapted, but the product owns authentication, tenancy, data minimization, persistence, operations,
rollback, and evidence.

`RUNTIME_GOVERNANCE_ENABLED=false` blocks new control leases and acknowledgements in this API. It
cannot recall authority already delivered to an external runtime, so it is not an emergency-stop
mechanism. A future adopter must add revocation-aware authorization immediately before execution,
define in-flight cancellation behavior, and stop the runtime consumer or wait for leases to expire
when disabling governance. With the repository default disabled, no control lease is issued.

`PROVIDER_EXECUTION_ENABLED=false` blocks traced OpenAI and Anthropic SDK construction and requests,
including direct no-context paths. `TRACE_CAPTURE_PROMPTS=false` omits full prompt, response, and
plaintext preview capture from new traced provider calls. Prompt-bearing trace detail and export
responses additionally require administrator access and explicit `include_prompts=true`. These are
forward-looking code boundaries, not proof that a historical database contains no sensitive data.
No deployment or product-owned store was identified for cleanup here; each adopter must audit,
redact, retain, or delete its own existing records under an approved sensitive-data contract.

The provider-execution gate must not be independently reverted while provider credentials or
provider-network egress remain available. Before rollback across this boundary, remove or revoke
provider credentials, block provider egress, stop or drain existing processes and in-flight calls,
and verify that no provider request occurs under the rollback candidate. Setting
`PROVIDER_EXECUTION_ENABLED=false` is insufficient for an older revision that does not implement
the gate.

The container entrypoint defaults `MIGRATE_ON_START=false` and fails closed on ambiguous Boolean
flag values. A repository-local Compose environment may explicitly opt its isolated database into
migrations, but no default or example authorizes mutation of a product or shared database.

## Second-Consumer Adoption Gate

A reusable shared component is permitted only after two independent active products demonstrate
the same stable need. Before extraction, record all of the following:

1. Two named consumers with production-relevant use cases and accountable owners.
2. The shared producer and consumer contract, including schema versioning and compatibility.
3. Product-owned persistence, tenant isolation, retention, deletion, and redaction behavior.
4. Failure, retry, idempotency, concurrency, timeout, cancellation, and cleanup semantics.
5. A current pricing/cost authority when monetary estimates are part of the contract.
6. Focused tests in each consumer plus cross-consumer contract tests.
7. Evidence that duplication is material and extraction reduces total operational complexity.
8. A rollback/removal path that does not make either consumer depend on an unowned platform.

One consumer, repository test coverage, or architectural similarity is insufficient. Until this
gate passes, integration stays product-local.

## Known Blockers

These blockers prevent treating the repository as a stable shared package or deployable product:

### Atomic aggregation

`src/tracing/storage/postgres.py` aggregates trace tokens and cost with a read-modify-write
sequence. Concurrent span completion can lose updates unless the database operation is made atomic
and proven with concurrency tests. Aggregate accuracy is therefore not a shared-service contract.

### Prompt retention and redaction

Provider wrappers can capture full prompts and responses, while other paths retain previews,
reasoning, errors, and metadata. There is no complete product-neutral classification, default
redaction, retention, deletion, encryption, authorization, or export contract. No product should
adopt payload capture without defining and testing those controls.

### Duplicate enum authorities

Trace and span enums are defined independently in `src/tracing/types.py` and
`src/models/trace.py`. Drift can break persistence or API behavior. A consumer must establish one
canonical authority before relying on compatibility.

### Pricing authorities

Pricing is hard-coded in the tracer and provider wrappers, with fallback prices and comments tied
to historical model snapshots. There is no versioned source, effective date, provenance, or update
process. Cost values are estimates, not billing truth.

### Unstable package contract

The package includes API, database, provider, CLI, scheduler, console-related, and operational
dependencies under one distribution. Public exports, migration ownership, semantic compatibility,
and deprecation policy are not defined. `Private :: Do Not Upload` is intentional; consumers
should not depend on the package as a supported external interface.

### Incomplete lifecycle and provider tests

Existing tests cover useful deterministic behavior but do not establish complete concurrent trace
aggregation, cancellation and process-loss recovery, every failure transition, live Anthropic or
OpenAI compatibility, provider streaming/tool-call variants, prompt redaction, or product-owned
end-to-end persistence. Provider mocks and local integration tests remain development evidence.

### In-flight control revocation

The API rejects new runtime-control claims and acknowledgements while governance is disabled, but
the repository does not contain a runtime consumer or a revocation-aware pre-execution handshake.
A runtime that already received a leased instruction may act before the lease expires even though
the API will reject its acknowledgement and will not project the action as applied. Runtime-control
delivery therefore remains deferred and must not be adopted as an emergency-stop or revocation
contract.

## No Standalone Roadmap

There is no roadmap for a hosted AI Trace service, operator console, fleet control plane, runtime
governance platform, public package, standalone deployment, or customer acquisition. Capability or
adoption work may be opened only from a named active-product requirement and must remain in that
product's canonical layer unless the second-consumer gate passes. This repository may otherwise
receive only explicitly authorized governance, dependency/security, or risk-reducing maintenance
that adds no capability, compatibility promise, deployment path, or product-adoption behavior.
