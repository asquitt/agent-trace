---
name: independent-review
description: Run the required read-only exact-base-to-head senior review for material AI Trace changes before merge or deployment.
---

# AI Trace Independent Review

Use this only after the implementation and focused verification are complete and the candidate is committed.

1. Record full immutable base and head SHAs. Do not review an uncommitted worktree or moving branch name.
2. Give one separate xhigh reviewer the user requirements, architecture affected, tests, runtime context, and `.github/ai-review/senior-review.md`.
3. The reviewer stays read-only, inspects `git diff BASE..HEAD` and directly affected call paths, and challenges the claimed property with one focused adversarial or real-runtime probe when practical.
4. Admit HIGH or MEDIUM only with a plausible current trigger, concrete customer, security, data-loss, or material-correctness impact, affected file and line, reproducible evidence, and the smallest sufficient fix.
5. Freeze the candidate on any admitted blocker. The primary agent makes the narrow fix, reruns relevant gates, commits a new head, and requests one bounded re-review from the same reviewer.
6. Do not extend cycles for LOW, speculative, unrelated, or non-reproducible observations. Hosted CI, previews, and deployment checks are separate evidence and never substitute for this review.
7. Do not merge or deploy until the exact current head has no HIGH or MEDIUM findings.

The reviewer output must match `.github/ai-review/review.schema.json`. Preserve the reviewed base/head SHAs and the final verdict in the handoff.
