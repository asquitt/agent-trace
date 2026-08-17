---
name: ship
description: Safely commit and optionally push verified AI Trace changes without absorbing unrelated work.
---

# Ship AI Trace

Use only when the user asks to commit, ship, or push.

1. Read `AGENTS.md`, `CLAUDE.md`, and the project-quality skill.
2. Inspect `git status -sb`, the working diff, staged diff, branch, and remote. Identify exactly which files belong to the task.
3. Run the focused and changed-stack verification required by the task. Do not expand into an unrelated global suite solely to manufacture a green status.
4. For material production, security, isolation, persistence, provider, deployment, or public-UI changes, require blocker-free independent review of the exact current base/head before merge or deployment.
5. Stage only task-owned files and review `git diff --cached --check`, `git diff --cached --stat`, and the full staged diff.
6. Create one or more coherent conventional commits without AI co-author trailers.
7. Push once only when the user requested it or at the authorized final handoff boundary. Preserve the current branch and configured remote; never force-push or rewrite history.
8. Report commit SHA and message, pushed branch and remote if any, verification and review evidence, and unrelated work intentionally left untouched.

Stop if ownership of a changed file is ambiguous or a required in-scope gate is unresolved.
