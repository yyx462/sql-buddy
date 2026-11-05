# ADR 0005: Gitea is the source of truth; GitHub is the public mirror and release channel

- Status: accepted
- Date: 2026-11-05
- Supersedes: nothing (extends [ADR 0003](0003-release-via-github-actions-trusted-publishing.md))

## Context

The project lives in two git hosts, and the relationship was undocumented —
re-derived (partly wrongly) on 2026-11-04 when a broken `git remote set-url`
silently dropped the GitHub push target. This ADR records the measured state
of both hosts and the sync mechanism, so nobody has to re-derive it.

## Measured state (2026-11-05)

| What | Gitea `git.sh.nint.com` | GitHub `yyx462/sql-buddy` |
|---|---|---|
| Role | source of truth | public mirror + release/publish channel |
| `master` | full dev history (merge commits, PR refs) | single squashed commit `v0.3.3 public release` (history-sanitized snapshot, 2026-08) |
| Branches | ~20 dev branches (`fix/*`, `afk/*`, …) | `master`, `beta`, dependabot branches only |
| Tags `v*` | full-history commits | same commits (not squashed) |
| Automation | none (gwt lane, human merge gate) | `release.yml` (PyPI Trusted Publishing on `v*` tag), dependabot |

## Decision / mechanism

1. **Sync is git-client fan-out, not server-side mirroring.** The bare repo
   (`.bear/config`) carries:
   - `remote.origin.fetch` → Gitea (fetch always from the source of truth);
   - `remote.origin.pushurl` → **two** URLs: Gitea **and**
     `git@github.com:yyx462/sql-buddy.git` — a plain `git push` lands on both;
   - a dedicated `remote "github"` for fetching what the public side looks
     like (e.g. checking what dependabot opened).
   No Gitea push-mirror or webhook exists — if nobody pushes, GitHub drifts.
2. **GitHub `master` is deliberately history-sanitized.** Dev history (with
   corp context) must not be published; public releases land as curated
   snapshots. Tags `v*` are the exception: they point at the real commits and
   are what the release workflow builds from. Do not "fix" the divergence by
   force-pushing full history to GitHub master.
3. **Release flow** (unchanged from ADR 0003): bump `_version.py` on Gitea
   master → tag `vX.Y.Z` → `git push --tags` (fan-out delivers the tag to
   GitHub) → `release.yml` publishes to PyPI as `sqlbuddy-nwi` via Trusted
   Publishing.

## Consequences

- **Installs must NOT point at Gitea.** The public artifact is PyPI
  (`uv tool install sqlbuddy-nwi`) or, for unreleased fixes, a GitHub tag.
  A local install from the Gitea URL (done on 2026-11-04 to get the
  thread-leak fix early) is a stopgap: `uv tool upgrade` against it will
  404/change whenever the remote layout changes — replace it with the PyPI
  install of v0.3.4 when released.
- **Guard the pushurls.** Any `git remote set-url origin <url>` **replaces
  the whole pushurl list** and silently drops the GitHub fan-out (this is
  exactly the 2026-11-04 incident). Add/change push targets only with
  `git config --add/--unset remote.origin.pushurl`, and verify with
  `git remote -v` showing two push lines before pushing tags.
- **Dev branches stay Gitea-only** in practice (GitHub has no `fix/*`
  branches); the fan-out only matters for `master`/tags pushed from the
  primary worktree.
