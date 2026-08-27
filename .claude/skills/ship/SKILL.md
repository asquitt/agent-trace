---
name: ship
description: Safely commit, push, and hand off verified AI Trace changes without absorbing unrelated work.
---

# Ship AI Trace

Use when the user asks to commit, ship, push, or deliver a pull request, or when an authorized implementation task reaches a coherent local commit boundary.

1. Read `AGENTS.md`, `CLAUDE.md`, the project-quality skill, and the `pr-lifecycle` skill.
2. Inspect `git status -sb`, the working and staged diffs, current branch, upstream, remote, worktrees, and any open PR for the branch. Identify task-owned files and preserve all unrelated work.
3. Run the focused and changed-stack verification required by the task. Do not expand into an unrelated global suite solely to manufacture a green status.
4. For material production, security, isolation, persistence, provider, deployment, or public-UI changes, require blocker-free independent review of the exact current base/head before merge or deployment.
5. Stage only task-owned files and review `git diff --cached --check`, `git diff --cached --stat`, and the full staged diff.
6. Create a conventional commit at each verified, bisectable checkpoint and at least once at the end of a successful session. Do not mix unrelated cleanup or add AI co-author trailers.
7. Keep shared or reviewed commits immutable. A review correction is a new coherent commit; never amend, rebase, force-push, or rewrite the candidate.
8. Batch commits and push once at the authorized session or correction-cycle boundary, not after every commit.
9. For a material branch, use the `pr-lifecycle` skill to open or update one draft PR at the first authorized push, freeze exact base/head/tree identities, and carry review, rollback, and cleanup evidence through merge.
10. Do not mark ready, merge, close, delete a branch, or delete a worktree unless the `pr-lifecycle` gates and task authority allow it. Preserve unresolved or ambiguously owned lanes.
11. Report commit SHA and message, pushed branch and remote if any, PR number and state if any, verification and review evidence, and unrelated work intentionally left untouched.

Stop if ownership of a changed file is ambiguous or a required in-scope gate is unresolved.
