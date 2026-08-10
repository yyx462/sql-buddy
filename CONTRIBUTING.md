# Contributing to SQL Buddy

## Open PRs freely

You (or your agent) can open pull requests without asking first. There is no
gatekeeper. Prefer a human or agent self-check against [AGENTS.md](./AGENTS.md)
before merge.

## Automated Review (parked)

The Jenkins **Review agent** job (`sql-buddy-review`) is **parked** while the
shared AFK implement loop is rolled out (Jenkins job **`algo-afk`** / harness
repo **afk-home** — see
[docs/adr/0005-afk-consumer-park-review.md](./docs/adr/0005-afk-consumer-park-review.md)).
Sources for review (`Jenkinsfile`, `ci/review_post.py`, review `Dockerfile`)
remain in git for a later unpark.

When unparked, every newly opened PR gets an automated **Review pass** — the
Review agent (pi) reads the diff and the code, checks it against the rubric in
[AGENTS.md](./AGENTS.md), and posts a comment with a verdict label:

- **`agent:approved`** — no blocking or minor issues. Ready for a final human
  review and merge.
- **`needs-work`** — blocking issues found (read-only invariant, secret safety,
  correctness). Please address them.
- **`good-attempt`** — only non-blocking minor issues (naming, style,
  terminology). Mergeable after small cleanups.
- **`review-error`** — the automated review could not complete. A human will
  take a look.

## What is NOT reviewed automatically

These PRs are skipped (the Review agent will not run on them):

- **PRs authored by the maintainer** (`ying.yuxiang`) — self-reviewed.
- **Draft PRs** — mark your PR as draft to opt out. It will be reviewed
  automatically once you mark it "ready for review."
- **PRs with the `skip-review` label** — add this label to any PR you don't
  want auto-reviewed.

## Before you code

If you're planning a non-trivial change, consider walking through your design
first — either with a colleague or with an AI agent using a "grilling" or
"design review" skill. A few minutes of design pressure up front saves a
`needs-work` label and a rewrite cycle.

## Coding standards

The standards are the review rubric: see [AGENTS.md](./AGENTS.md). In short:

1. **Never break the read-only invariant.** Every SQL path must go through the
   Locked layer. No write keyword may ever reach a real database.
2. **Never expose secrets.** DB credentials, SSH keys, and `db.ini` configs
   must never be logged, committed, or returned in responses.

## Never leak real infrastructure details

Examples, docstrings, comments, and code samples must use **placeholder** values
— never real IPs, real bastion ports, real private key filenames, real DB hostnames,
or real internal network layout. Even a "private" comment in a template file gets
cloned into every fork and every CI cache, and is impossible to un-publish once it
ships in a commit's diff.

**Acceptable** placeholders (this repo's convention):

| Real-world thing            | Use this instead                       |
|-----------------------------|----------------------------------------|
| Public bastion IP / host    | `192.0.2.10` (RFC 5737 TEST-NET-1) or `bastion.example.com` |
| SSH port                    | `22222` (obviously fake; **do not assume 22**) |
| Internal DB IPs              | `10.0.0.21`, `10.0.0.38`, `10.0.0.11`, `10.0.0.95` |
| Private key filename        | `~/.ssh/id_ed25519` or `~/.ssh/prod_key` |
| SSH user                    | `your_ssh_user` or a generic `first.last` |
| DB user                     | a role label like `api_asia`, `nintweb`, `script_pro`, `algo_drw` — never a real one |

When in doubt: placeholders, not raw infra. Anyone reading your diff on a phone
screen should learn nothing they did not already know about your production network.

**Before you push, audit your staged diff** for any token that looks
infrastructure-shaped:

```bash
git diff --cached | grep -nE \
  '([0-9]{1,3}\.){3}[0-9]{1,3}|-[0-9]{4,5}|id_rsa_(db|[a-z]+)|~/.ssh/id_'
```

If anything that matches is a real value (not one of the placeholders above), stop
and redact before pushing. The Review agent does NOT check for this — humans must.

If a leak has already been pushed: do **not** just fix it in a follow-up commit.
That leaves the original in history. Open a PR to rewrite history (`git filter-repo
--replace-text …`) and force-push — see the maintainer for guidance.
3. **Use the domain language correctly.** Read
   [UBIQUITOUS_LANGUAGE.md](./UBIQUITOUS_LANGUAGE.md) — a **Connection** is not
   a **DBMS database**. Don't reintroduce retired terms like `db_name`.

## Merging

Only a human merges. The Review agent never merges — it only reviews and
labels. Even an `agent:approved` PR needs a quick human sign-off before merge.
