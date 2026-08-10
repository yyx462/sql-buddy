# AFK implement loop on Jenkins; park the Review agent job

sql-buddy is an AFK **product** consumer. Unattended **issue → implement →
Gitea PR** runs via the shared harness repo **afk-home** and Jenkins job
**`algo-afk`** on web01. The existing **Review agent** pipeline
(`sql-buddy-review` / root `Jenkinsfile`) is **parked** (disable in Jenkins
UI; keep sources in git) while AFK is proven. GitHub remains the
release/mirror path for PyPI (`sqlbuddy-nwi`); AFK never opens GitHub PRs or
publishes.

## Why

- Prove portable AFK (Sandcastle harness + thin tea/SSH wrapper) against company
  Gitea + Jenkins without rewriting Sandcastle.
- One multi-repo job serves many product repos; product trees stay free of AFK
  Jenkinsfiles.
- Free operator attention from dual automation until implement is reliable.
- Preserve review code and security posture (read-only tools, scoped token) for
  a later unpark or Sandcastle-based review recipe.

## Consequences

- Shared job **`algo-afk`** loads `Jenkinsfile` from **afk-home** (not this
  repo). Workspace `/data/www/algo/afk-work`; product clone under `work/`.
- Product webhook: Issues →
  `https://cicd.nint.hk/generic-webhook-trigger/invoke?token=algo-afk`
  when label `ready-for-agent` is added.
- No product-repo `Jenkinsfile.afk`. Optional legacy `afk/` tree in this repo is
  not the production harness.
- AFK Gitea token is separate from the review comment/label token; git push
  uses SSH.
- Sandbox must not mount prod `db.ini` or host credential files.
- Human still merges every PR.

## Out of scope

- Duplicating the harness inside each product repo.
- Migrating review onto Sandcastle.
- Agent merge; PyPI/GitHub automation from AFK.

## See also

- afk-home: `docs/SETUP-GUIDE.md`
