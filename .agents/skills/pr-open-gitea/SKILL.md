# Open a PR via the Gitea API

Open a pull request on this repo's Gitea instance using only the
`GITEA_TOKEN` env var — no `gh`, no `tea`, no manual web UI. Project-scoped:
this skill is for `sql-buddy` only and bakes in its repo + base branch.

This skill also ships `pr_merged.py`, a small read-only script the agent runs
to authoritatively check whether a PR is merged — to defeat the
"PR-not-merged" hallucination (the agent's local `master` may be stale or it
may misread Gitea's UI text into thinking an already-merged PR is still open).

## When to use

- The agent has finished committing on a local branch and is asked to open
  a pull request against `master`.
- A web PR form / CLI is unavailable or undesirable.
- Before any statement of the form "PR #N is/ isn't merged", **run
  `pr_merged.py`** instead of trusting memory or local git state.

Do **not** use this skill for any other repository — write a new skill there
with the correct `REPO` / `BASE` / `INSTANCE`, do not parameterize this one.

## Preconditions

- `$GITEA_TOKEN` is set in the environment with at least these scopes:
  `write:issue`, `write:repository`. (Read scopes like `read:user` are NOT
  needed and will 403 on `/api/v1/user` — that 403 is expected and harmless.)
- The current branch is pushed to `origin`.
- `python3`, `curl`, and `git` are on PATH.

## Constants (project-specific)

- `INSTANCE`   = `https://git.sh.nint.com`
- `REPO`       = `ying.yuxiang/sql-buddy`
- `BASE`       = `master`
- `MAINTAINER` = `ying.yuxiang`  (PRs authored by this login are auto-skipped
  by the review pipeline — see Jenkinsfile:89. That is expected; the
  maintainer self-reviews.)

## Procedure

1. **Confirm the branch is pushed.** The head SHA must exist on `origin`:
   ```bash
   git rev-parse --verify origin/$(git rev-parse --abbrev-ref HEAD)
   ```
   If this errors, push first: `git push -u origin HEAD`.

2. **Build the PR body.** Always feed the JSON payload through `python3` so
   backticks, quotes, and `${VAR}` in the body are NOT interpreted by the
   shell. (The shell-eval bug — `command not found: minimax` — happens
   when the body is inlined in a curl `-d` string.)
   ```bash
   python3 - <<'PY' > /tmp/pr_body.json
   import json
   print(json.dumps({
       "title": "<PR title>",
       "body":  "<markdown body — backticks OK>",
       "base":  "master",
       "head":  "<branch-name>",
   }))
   PY
   ```

3. **POST to the Gitea API.**
   ```bash
   curl -s -X POST \
     -H "Authorization: token $GITEA_TOKEN" \
     -H "Content-Type: application/json" \
     -d @/tmp/pr_body.json \
     https://git.sh.nint.com/api/v1/repos/ying.yuxiang/sql-buddy/pulls \
     -w "\nHTTP %{http_code}\n"
   ```
   - `201 Created` → PR opened. Parse the JSON response for `html_url`,
     `number`, `mergeable`.
   - `409 Conflict` → a PR already exists for this head branch. Fetch it:
     `GET .../pulls?state=open&head=<owner>:<branch>` and decide whether to
     update, comment, or do nothing.
   - `403` → token missing a required scope. The error JSON lists the
     required scopes; do not retry with the same token.

4. **Report back** the PR URL (`html_url` in the response) and number.

## Body template (copy this)

```
## Why
<one paragraph: what's wrong or missing, linked to issue/PR if any>

## Changes
- <bullet per file or per logical change>

## Verification
<commands the reviewer can run to reproduce; for code that ships to prod,
include the smoke test you ran and its observed output>
```

## Token hygiene (per AGENTS.md Tier-2 security rule)

- Never `echo "$GITEA_TOKEN"` in any output stream.
- Never write the token into a file, commit, or PR body.
- Never pass the token via a command-line `-d` string that the process table
  could leak — use `-d @/tmp/pr_body.json` (body only) + the standard
  `Authorization:` header (curl redacts on most systems).
- If the token is found in any log, treat it as a leak: rotate it before
  continuing, regardless of who saw it.

## Out of scope

- Adding labels, reviewers, or assignees (separate calls; not needed for v1).
- Merging (the maintainer does this manually; AGENTS.md: "Only a human
  merges").
- Commenting on an existing PR (use a future `pr-comment-gitea` skill).
- Cross-fork PRs (this skill assumes same-owner head and base).

## Bundled script: `pr_merged.py`

Authoritative merged-state check via the Gitea API (`GET /pulls/{index}`).
Sits next to this `SKILL.md` in `.agents/skills/pr-open-gitea/`.

Why it exists: the agent has been observed claiming an already-merged PR is
still open. Stale local `master`, misread UI text, or just plain
hallucination — the fix is to ask the API, not the model's memory.

### Usage

```bash
python3 .agents/skills/pr-open-gitea/pr_merged.py <PR_NUMBER>
# optional: GITEA_TOKEN=... python3 pr_merged.py 4
```

### Output (one line, machine-parseable)

- Merged:  `PR #4: MERGED into ying.yuxiang/sql-buddy at <TS>` + merge_commit
  + head_sha lines, exit `0`.
- Not merged (open or closed-without-merge): `PR #4: NOT MERGED — state=open,
  merged=False`, exit `1`.
- API error / 404 / bad PR number: stderr message, exit `2`.

### Auth

- Uses `$GITEA_TOKEN` if set. The token's read scope is sufficient; `write:*`
  scopes (which is what we have for opening PRs) also work.
- If the token is missing or 401s, falls back to an unauthenticated GET — the
  Gitea repo is public so reads succeed without auth.

### Agent rule

Before writing or saying "PR #N is merged" / "PR #N is not merged", **always
run this script first**. Never derive merge state from local `git log`,
`git branch -r`, `git rev-parse origin/master`, or memory. Those lie. The
API does not.

### Safety

- Read-only: issues a single `GET`. Never mutates anything.
- Token is read from env, never echoed. Same hygiene rules as the rest of
  this skill (see Token hygiene above).