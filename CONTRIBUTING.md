# Contributing to SQL Buddy

## Open PRs freely

You (or your agent) can open pull requests without asking first. There is no
gatekeeper — the automated Review agent will provide a first-pass review on
every PR.

## What gets reviewed automatically

Every newly opened PR (and every push to an existing PR) gets an automated
**Review pass** — the Review agent (pi) reads the diff and the code, checks it
against the rubric in [AGENTS.md](./AGENTS.md), and posts a comment with a
verdict label:

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
3. **Use the domain language correctly.** Read
   [UBIQUITOUS_LANGUAGE.md](./UBIQUITOUS_LANGUAGE.md) — a **Connection** is not
   a **DBMS database**. Don't reintroduce retired terms like `db_name`.

## Merging

Only a human merges. The Review agent never merges — it only reviews and
labels. Even an `agent:approved` PR needs a quick human sign-off before merge.
