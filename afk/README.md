# sql-buddy AFK (issue → implement → Gitea PR)

Unattended implement loop for this repo. **Sandcastle** runs **Pi** in Docker;
the **host** resolves the Gitea issue via **tea**, claims labels, and opens the
PR after commits. A human merges.

Architecture: yyxSkills ADR 0004 (depend on Sandcastle; thin forge wrapper).
This repo: `docs/adr/0005-afk-consumer-park-review.md`.

## Layout

| Path | Role |
|------|------|
| `afk/.sandcastle/` | Dockerfile, `main.ts`, prompt, `.env` (local) |
| `afk/scripts/tea.sh` | Careful tea harness (no token echo) |
| `afk/scripts/open-pr.sh` | Push + `tea pulls create` |
| `Jenkinsfile.afk` | Job `sql-buddy-afk` on web01 |
| Repo root | Sandcastle **cwd** (product tree) |

Not shipped in the `sqlbuddy-nwi` wheel.

## Prerequisites

- Node 20+, Docker, `tea` CLI
- Gitea token (issue RW, repository RW, user R, misc R) — see setup wizard
- Git push via SSH to `ying.yuxiang/sql-buddy`
- ZAI Coding Plan key (`zai-coding-cn` / `ZAI_CODING_CN_API_KEY`)

## Setup

```bash
# One-time web01 / checklist
bash afk/scripts/setup-web01.sh

cd afk
npm install
cp .sandcastle/.env.example .sandcastle/.env   # fill secrets; never commit
npm run sandbox:build                          # → sql-buddy-afk:pi
```

### Gitea token scopes

| Permission   | Access         |
|-------------|----------------|
| issue       | Read and Write |
| repository  | Read and Write |
| user        | Read           |
| misc        | Read           |
| everything else | No Access  |

File on Jenkins: `/data/www/algo/deps/gitea-token-afk` (chmod 600).

### Labels

- `ready-for-agent` — human go signal  
- `afk:in-progress` — AFK claimed  

```bash
AFK_GITEA_TOKEN_FILE=... ./scripts/tea.sh ensure-login
./scripts/tea.sh labels-ensure
```

## Run (laptop)

```bash
export AFK_ISSUE=123
# export AFK_GITEA_TOKEN_FILE=...
npm run afk:dry    # prompt only
npm run afk        # Docker + Pi + open-pr
# npm run afk:host # no Docker (debug)
npm test
```

Flow:

1. Host: `tea` view/claim issue (`ready-for-agent` → `afk:in-progress`)
2. Sandcastle worktree + Docker Pi implement + commit on `afk/issue-N`
3. Host: SSH push + `tea pulls create` (`Fixes #N`); drop in-progress
4. Human merges

## Jenkins

- Job: `sql-buddy-afk`, script path **`Jenkinsfile.afk`**
- Phase 1: **Build with Parameters** → `AFK_ISSUE`
- Phase 2: issue webhook token `sql-buddy-afk` when label `ready-for-agent` added
- Park (disable) job `sql-buddy-review`; do not delete sources

## Security

- Review image stays read-only tools; AFK image is separate (write/git).
- No prod `db.ini` in the sandbox; optional `db.ini.example` mount only.
- Gitea AFK token never enters the Pi container; only ZAI key does.
- Never log token/key values.

## Proof path

1. Docs-only issue with `ready-for-agent` → manual Jenkins/`npm run afk` → PR  
2. Small code issue → same  
3. Enable label webhook  
4. Later: extract portable `afk` (Jenkins + no-Jenkins tutorials)
