# Review agent v1 runs read-only with no Gitea push capability

The Review agent (pi) is deliberately constrained in v1: it runs with only
`read,grep,find,ls` tools (no `bash`/`write`), its container receives only the
LLM API key (a Jenkins credential), and the Gitea token - scoped to
comment+label, no push - stays on the Jenkins side and is used by the pipeline
to post feedback. The agent never holds a token that can mutate the repo.

## Why

Two fixed constraints force this posture:

- **pi has no permission system** - "it runs with the permissions of the user
  that launched it"; the container is the only boundary.
- **No machine/bot account is available** on the company Gitea, so the agent
  acts under the maintainer's identity and must be tightly scoped.

Capability is limited at three layers: tool allowlist (can't write files or run
commands), token scope (comment+label only, no push/merge), and token placement
(the Gitea token never enters the agent container - only the LLM key does). An
errant or prompt-injected agent can burn LLM credits and read the repo, but
cannot comment-as-merge, push, or merge.

## Considered options

- **Agent-direct feedback + auto-fix-push** (pi holds a push token and writes
  fixes): rejected for v1 - blast radius too large before the loop is trusted.
  Deferred to a future iteration behind its own hardening (write-gating pi
  extension, branch protection, `--force-with-lease`).
- **Dedicated review-bot Gitea account**: preferred for clean attribution, but
  not creatable on the company Gitea. v1 posts via the maintainer's scoped
  token with an explicit "Automated Review pass" banner; revisit when auto-fix
  lands or if an admin grants a machine account.

## Consequences

- Reviews post under the maintainer's identity, disambiguated by a banner and
  the verdict labels.
- Upgrading to auto-fix/refine is a deliberate, multi-step change: widen the
  `--tools` allowlist, mint a push-capable token, enable branch protection.
- Until a bot account exists, future agent-authored commits will attribute to
  the maintainer in `git blame`; acceptable for v1 (the agent authors none).
