---
name: pr-lifecycle
description: Open, update, ready, merge, or close a pull request using the project's Orbitr-derived exact-commit delivery flow.
---

# Pull Request Lifecycle

Use this skill when a task includes opening, updating, readying, merging, closing, or handing off a pull request.

## 1. Resolve the exact lane

1. Read `AGENTS.md`, `CLAUDE.md`, the project-quality skill, and the independent-review skill.
2. Inspect `git status -sb`, `git worktree list --porcelain`, the current branch, its upstream, remotes, and any open PR for the branch. Preserve unrelated, dirty, active, or ambiguously owned work.
3. Discover the repository default branch rather than assuming it:

```bash
PR_DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')"
PR_BRANCH="$(git branch --show-current)"
git fetch origin "$PR_DEFAULT_BRANCH"
PR_BASE_SHA="$(git merge-base HEAD "origin/$PR_DEFAULT_BRANCH")"
PR_HEAD_SHA="$(git rev-parse HEAD)"
PR_HEAD_TREE="$(git rev-parse "HEAD^{tree}")"
```

4. Material work uses a short-lived `codex/<short-slug>` branch based on the latest verified default branch. Do not open a material PR from the default branch.

## 2. Apply the commit cadence

- Commit every verified, bisectable checkpoint and at least once at the end of a successful work session.
- Keep one customer-impact or operational slice per branch and PR. Do not mix unrelated cleanup.
- Use focused conventional commits without AI co-author trailers.
- Do not amend, rebase, force-push, or otherwise rewrite a shared or reviewed candidate. A review fix is a new coherent commit.
- Batch commits and push once at the authorized session or correction-cycle boundary, not after every commit.

## 3. Push once and open or update the draft

1. Run the focused and changed-file gates, then inspect the full staged diff before committing.
2. At the authorized shipping boundary, push the current branch once:

```bash
git push -u origin HEAD
```

3. Query before creating. Reuse the open PR for this branch; never create a duplicate:

```bash
PR_NUMBER="$(gh pr list --state open --head "$PR_BRANCH" --json number --jq '.[0].number // empty')"
gh pr list --state open --head "$PR_BRANCH" --json number,title,isDraft,url,headRefOid
```

4. If none exists and the slice is material, complete `.github/pull_request_template.md` in a temporary body file and open a draft PR against `$PR_DEFAULT_BRANCH`. Include exact base, head, and tree SHAs, scope, tests, runtime or preview evidence, review state, rollback, and cleanup:

```bash
gh pr create --draft --base "$PR_DEFAULT_BRANCH" --head "$PR_BRANCH" --title "<type: concise outcome>" --body-file "<completed-body-file>"
```

5. When the candidate changes, update the existing PR body rather than opening another:

```bash
gh pr edit "$PR_NUMBER" --body-file "<completed-body-file>"
```

6. Keep low-risk documentation with its implementation PR. Do not create a docs-only PR unless repository protection requires one.

## 4. Freeze and review

- Keep the PR draft while any required test, preview, runtime proof, rollback plan, cleanup, or exact-commit review is missing.
- Fetch the remote refs and verify that the PR's remote head equals the candidate SHA before review.
- For material changes, obtain one read-only independent xhigh review of the exact current base-to-head diff. Hosted checks, CI, or deployment previews do not replace it.
- HIGH or MEDIUM findings block readiness and merge. Fix only admitted blockers in a new commit, rerun affected gates, push once, freeze the new head, update the PR evidence, and request bounded re-review.
- If the base or head changes after review, the prior verdict does not approve the new diff or merge tree. Freeze and review the new exact candidate.

## 5. Ready and merge

1. Mark ready only when the current remote head is the reviewed head, all required evidence is green, conversations are resolved, and no ownership conflict remains.

```bash
gh pr ready "$PR_NUMBER"
```

2. Immediately before merge, refresh the PR and default branch. Verify head identity, mergeability, and that no required evidence has gone stale.

```bash
gh pr view "$PR_NUMBER" --json headRefOid,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup
```

3. Merge only when the task includes merge or release authority:

```bash
gh pr merge "$PR_NUMBER" --merge
```

Do not squash, rebase, force, or use `--delete-branch`. The merge commit preserves the candidate commits and review identities.

## 6. Close or preserve

- An accepted PR is merged; GitHub closes it automatically.
- Manually close only an abandoned, duplicate, or explicitly superseded PR. Add a final comment naming the reason, preserved head SHA, unresolved blockers, and successor PR or branch when one exists.

```bash
gh pr close "$PR_NUMBER" --comment "<reason; preserved head SHA; blockers; successor>"
```

- Never close a PR to make a blocker disappear or describe an unmerged candidate as shipped.
- Preserve branches and worktrees by default. Delete only when merged or superseded, clean, idle, and explicitly released, after recording the recovery SHA.

## 7. Post-merge closeout

Record and verify:

```bash
gh pr view "$PR_NUMBER" --json number,state,isDraft,headRefOid,mergeCommit,url
```

- PR number, candidate head/tree, merge SHA/tree, and default-branch identity
- required deployment or public/runtime behavior against the merge SHA
- rollback target and command or procedure
- fixture, task, provider, branch, and worktree cleanup state
- any explicitly unverified or blocked evidence

Return exact evidence, not a generic success statement.
