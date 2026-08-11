# Releases publish to PyPI via GitHub Actions + Trusted Publishing (OIDC)

The repo's source of truth is company Gitea (`git.sh.nint.com`), mirrored to
GitHub. Releases are cut by pushing a semver git tag, which mirrors
to GitHub and triggers a GitHub Actions workflow that builds the sdist+wheel
and publishes to PyPI via Trusted Publishing (OIDC) — no stored PyPI
credential anywhere.

## Why

PyPI's Trusted Publishing trusts `github.com` directly via short-lived OIDC
identity tokens minted per job-run. This eliminates the long-lived API token
that the alternatives require — nothing to rotate, nothing for a misconfigured
step or prompt-injection to exfiltrate. The GitHub mirror already exists, so
the trusted CI is available without moving the repo off Gitea.

## Considered options

- **Jenkins + PyPI API token**: rejected — Jenkins has no native
  OIDC-to-PyPI, so a long-lived token stored as a Jenkins credential is
  unavoidable. Keeps the release on primary CI but reintroduces the secret the
  OIDC path avoids.
- **Manual `uv build && uv publish`**: rejected as a permanent strategy — not
  reproducible, bus-factor of one, easy to skip the
  `_version.py`/`pyproject.toml` version sync. Acceptable only as a one-off v0
  throwaway before the workflow exists.
- **GitHub Release-triggered workflow** (vs tag-triggered): equivalent auth;
  tag-from-Gitea preferred so Gitea remains the release origin and GitHub is
  purely the runner.

## Consequences

- **Gitea->GitHub tag mirroring** is satisfied by the repo's `origin` push
  remote, which fans out to both Gitea (fetch+push) and GitHub (push). So
  `git push --tags` lands the tag on GitHub directly and fires `release.yml` —
  no separate mirror step or refspec needed. (Sanctioned fallback if this ever
  breaks: migrate the source of truth to GitHub — one home for source + CI +
  releases — rather than patching the mirror.)
- Versioning stays a deliberate static literal in `_version.py` +
  `pyproject.toml` (per the existing rationale: vendored/offline/bare
  `python server.py` runs must report a version); the release workflow bumps
  both atomically rather than switching to dynamic `setuptools-scm`.

## Implemented

- `.github/workflows/release.yml` — build + trusted-publish on `v*` tags
  (`environment: release`, `id-token: write`).
- `.github/workflows/test.yml` — install + smoke matrix
  (Ubuntu/macOS/Windows × Python 3.11/3.12/3.13) on every push/PR. The
  "infinite clean machines" check; also catches dep-boundary breaks like the
  `mcp>=1.0.0,<2` pin.
