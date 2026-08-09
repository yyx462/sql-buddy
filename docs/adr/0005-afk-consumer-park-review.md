# AFK implement loop on Jenkins; park the Review agent job

sql-buddy is the second AFK consumer (after yyxSkills spike). Unattended
**issue → implement → Gitea PR** runs via Sandcastle + Pi on web01. The existing
**Review agent** pipeline (`sql-buddy-review` / root `Jenkinsfile`) is
**parked** (disable in Jenkins UI; keep sources in git) while AFK is proven.
GitHub remains the release/mirror path for PyPI (`sqlbuddy-nwi`); AFK never
opens GitHub PRs or publishes.

## Why

- Prove portable AFK (Sandcastle harness + thin tea/SSH wrapper) against company
  Gitea + Jenkins without rewriting Sandcastle.
- Free operator attention from dual automation until implement is reliable.
- Preserve review code and security posture (read-only tools, scoped token) for
  a later unpark or Sandcastle-based review recipe.

## Consequences

- New job `sql-buddy-afk`, script path `Jenkinsfile.afk`, workspace
  `/data/www/algo/sql-buddy-afk`.
- In-tree harness under `afk/` (not part of the `sqlbuddy-nwi` wheel).
- AFK Gitea token is separate from the review comment/label token; git push
  uses SSH.
- Sandbox must not mount prod `db.ini` or host credential files.
- Human still merges every PR.

## Out of scope

- Extracting a portable `afk` npm package (follow-up after this consumer is green).
- Migrating review onto Sandcastle.
- Agent merge; PyPI/GitHub automation from AFK.
