# Open a PR via the Gitea API

Open a pull request on this repo's Gitea instance using only the
`$GITEA_TOKEN` env var — no `gh`, no `tea`, no manual web UI. Project-scoped:
this skill is for `sql-buddy` only and bakes in its repo + base branch.

## When to use

- The agent has finished committing on a local branch and is asked to open
  a pull request against `master`.
- A web PR form / CLI is unavailable or undesirable.

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