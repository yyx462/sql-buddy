# Pull-Request Automation

The context covering the automated review of pull requests on this repository: a
review agent reads each PR and posts feedback plus a verdict label, so a human
only does the final merge. The query-server domain — Connections, Queries, the
read-only invariant — is documented separately in `UBIQUITOUS_LANGUAGE.md`.

## Actors

**Review agent**:
The agent that performs an automated review pass on a pull request.
_Avoid_: bot, AI, reviewer (reserve "reviewer" for a human).

**Coworker**:
A human who opens a pull request, distinct from the Review agent.
_Avoid_: contributor, developer, user.

## Workflow

**Review pass**:
The automated first-pass review of a pull request by the Review agent. It produces
feedback and a verdict label. A human merges; the Review agent never merges.
_Avoid_: check, scan, CI run.

**Auto-fix** *(future)*:
The Review agent editing a pull request's own branch to fix minor issues. Out of
scope for the first cut.
_Avoid_: self-heal, autocorrect.

**Refine** *(future)*:
The Review agent producing a clean replacement pull request that keeps the good
parts of a coworker's PR and fixes the rest. Out of scope for the first cut.
_Avoid_: rewrite, absorb.

## Labels

**Verdict labels** — applied by the Review agent to every reviewed PR, reflecting
only the review outcome (never who the author is; tone is uniform):

**`agent:approved`**:
Verdict: no blocking or minor issues. Ready for a human final review and merge.
_Avoid_: approved, lgtm, green.

**`needs-work`**:
Verdict: blocking issues found. The coworker must address them before merge.
_Avoid_: changes-requested, rejected, red.

**`good-attempt`**:
Verdict: only non-blocking minor issues. Effort acknowledged; mergeable after
small cleanups. Not a judgement of the author's seniority.
_Avoid_: almost, junior, partial.

**`skip-review`**:
Opt-out label. A pull request carrying it is never reviewed.
_Avoid_: no-review, ignore.

**`review-error`**:
Operational label: the Review pass could not produce a verdict (crash, timeout,
or malformed output). Signals an automation failure, not a review outcome; a
human investigates and re-triggers.
_Avoid_: failed, broken, error.
