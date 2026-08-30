## Customer and system impact

What behavior changes, who or what is affected, and which product or operational boundary owns it?

## Scope and exclusions

- In scope:
- Explicitly excluded:
- Unrelated work preserved:

## Disposition and ownership

- Change class: repository governance/dependency/security/risk-reducing maintenance OR named product-local adoption OR shared extraction
- Maintenance proof that this adds no capability, compatibility promise, deployment path, or product-adoption behavior (or not applicable):
- Named active product and accountable owner (required for every capability expansion or adoption; otherwise `none - bounded maintenance only`):
- Product-local implementation location and acceptance criteria (or not applicable):
- Operator or customer outcome and evidence (or not applicable):
- Shared extraction requested: yes / no
- If yes, evidence for all eight second-consumer gate items in `docs/DISPOSITION.md`:

## Exact candidate

- Default branch and base SHA:
- Candidate branch and head SHA:
- Candidate tree SHA:
- Merge strategy: merge commit

## Verification evidence

| Property | Command or probe | Result | Evidence identity |
|---|---|---|---|
| Focused behavior |  |  |  |
| Invalid or failure case |  |  |  |
| Changed-file quality gates |  |  |  |
| Preview or runtime behavior, if required |  |  |  |

## Independent review

- Required for this change: yes / no, with reason
- Exact reviewed base and head:
- Verdict:
- Remaining HIGH findings:
- Remaining MEDIUM findings:

## Release safety

- Data or migration impact:
- Security, tenant, provider, or persistence impact:
- Observability signal and expected steady state:
- Rollback target and procedure:
- Provider-gate rollback (or not applicable; older revisions may ignore `PROVIDER_EXECUTION_ENABLED=false`): credentials removed/revoked, egress blocked, processes and in-flight calls drained, and zero provider requests verified against the rollback candidate
- Fixture, task, provider, and external-state cleanup:

## AI Trace acceptance

- [ ] The change preserves `docs/DISPOSITION.md`: no standalone product, hosted service, public package, deployment promotion, or generic control-plane work.
- [ ] A maintenance change is limited to governance, dependency/security, or risk reduction and adds no capability, compatibility promise, deployment path, or product-adoption behavior.
- [ ] A first adoption stays product-local and names its active product, owner, acceptance criteria, and outcome.
- [ ] Shared extraction occurs only after two independent active products satisfy every second-consumer gate item; otherwise no shared abstraction is added.
- [ ] Tenant scope, active leader versus standby/contending/error truth, advisory locks, and stale-session behavior are proven where applicable.
- [ ] Monotonic watermarks, approval-gated shutdown, dependency/image identity, and the development-only dashboard boundary remain intact.

## Readiness checklist

- [ ] This PR contains one coherent customer-impact or operational slice.
- [ ] The remote PR head matches the candidate SHA above.
- [ ] Focused tests cover the changed property and a relevant failure case.
- [ ] Required changed-file gates pass; unavailable evidence is labeled unavailable, not green.
- [ ] Material changes have blocker-free independent review of the exact current base-to-head diff.
- [ ] Required preview, deployment, or public behavior is proven against the named revision.
- [ ] Rollback and cleanup are concrete.
- [ ] No unrelated dirty work or secrets are included.
- [ ] The PR is still draft if any required item above remains unresolved.
