# Implement Gitea issue #{{ISSUE_NUMBER}}

**Title:** {{ISSUE_TITLE}}  
**URL:** {{ISSUE_URL}}

## Issue body

{{ISSUE_BODY}}

## Your job

1. Read the acceptance criteria in the issue body. Treat them as the definition of done.
2. Read `AGENTS.md` and `UBIQUITOUS_LANGUAGE.md` before touching security-sensitive paths (Locked layer, credentials, Connection vs database naming).
3. Implement the change in this repository. Prefer small, reviewable commits.
4. Run any obvious cheap checks the repo already has if present.
5. Commit on the current branch. Use a clear message that mentions `#{{ISSUE_NUMBER}}`.
6. Do **not** open a pull request. Do **not** push. Do **not** close the issue. Do **not** change labels. A host wrapper opens the PR after you finish.

## Hard constraints (sql-buddy)

- Never weaken the **Locked** read-only invariant.
- Never log, commit, or echo database credentials, SSH keys, or `db.ini` secrets.
- Do not invent live DB access — no real credentials are available in this sandbox.
- Prefer the example/read-only config mount if present; do not hunt the host for secrets.

## Out of scope

- Unrelated refactors
- Expanding scope past the acceptance criteria
- Installing global tools or changing CI secrets
- Opening/merging PRs

When the acceptance criteria are met and commits are on the branch, stop.
