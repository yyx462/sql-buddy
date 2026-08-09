#!/usr/bin/env bash
# Interactive one-time setup checklist for sql-buddy-afk on web01 (or laptop dry-run).
# Safe to re-run. Does NOT print secret values.
set -euo pipefail

DEPS="${AFK_DEPS_DIR:-/data/www/algo/deps}"
GITEA_URL="${AFK_GITEA_URL:-https://git.sh.nint.com}"
REPO="${AFK_REPO:-ying.yuxiang/sql-buddy}"

c() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok() { printf '  [ok] %s\n' "$*"; }
todo() { printf '  [ ] %s\n' "$*"; }
warn() { printf '  [!] %s\n' "$*"; }

c "1. Gitea AFK token (NOT the review comment-only token)"
cat <<EOF
  Create at: ${GITEA_URL}/user/settings/applications
  Token name suggestion: sql-buddy-afk

  Selected token permissions (minimum):
    activitypub     No Access
    issue           Read and Write
    misc            Read
    notification    No Access
    organization    No Access
    package         No Access
    repository      Read and Write
    user            Read

  Write the token to a file (chmod 600), e.g.:
    ${DEPS}/gitea-token-afk

  Push uses git SSH (Jenkins 'sea' credential) — do not rely on this token for push.
EOF
if [[ -f "${DEPS}/gitea-token-afk" ]]; then
  ok "file exists: ${DEPS}/gitea-token-afk (contents not shown)"
else
  todo "create ${DEPS}/gitea-token-afk"
fi

c "2. ZAI Coding Plan key (Pi provider zai-coding-cn)"
cat <<EOF
  From open.bigmodel.cn Coding Plan.
  File (chmod 600): ${DEPS}/zai-coding-cn-key
  Env names at runtime: ZAI_CODING_CN_API_KEY / ZAI_API_KEY
  Never put apiKey literal in models.json.
EOF
if [[ -f "${DEPS}/zai-coding-cn-key" ]]; then
  ok "file exists: ${DEPS}/zai-coding-cn-key"
else
  todo "create ${DEPS}/zai-coding-cn-key"
fi

c "3. Host binaries under ${DEPS}"
for b in docker tea; do
  if [[ -x "${DEPS}/${b}" ]] || command -v "${b}" >/dev/null 2>&1; then
    ok "${b} available"
  else
    todo "install ${b} (tea: https://gitea.com/gitea/tea/releases ; docker static CLI like review job)"
  fi
done
if [[ -x "${DEPS}/node/bin/node" ]] || command -v node >/dev/null 2>&1; then
  ok "node available"
else
  todo "install Node 20+ under ${DEPS}/node (or PATH)"
fi

c "4. Gitea labels on ${REPO}"
cat <<EOF
  Create labels if missing:
    ready-for-agent   — human marks issue ready for AFK
    afk:in-progress   — AFK claimed the issue
  Or after token is set:
    cd afk && AFK_GITEA_TOKEN_FILE=... ./scripts/tea.sh ensure-login
    ./scripts/tea.sh labels-ensure
EOF

c "5. Jenkins job sql-buddy-afk"
cat <<EOF
  1. New Item → Pipeline → Pipeline script from SCM
  2. Repo: git@git.sh.nint.com:ying.yuxiang/sql-buddy.git
     Credential: sea (SSH). Branch: master. Script path: Jenkinsfile.afk
  3. Node: web01. Custom workspace: /data/www/algo/sql-buddy-afk
  4. This build is parameterized: AFK_ISSUE
  5. Save → Build with Parameters once (registers GenericTrigger token sql-buddy-afk)
  6. Phase 2 webhook (after manual green):
       ${GITEA_URL/https:\/\//https:\/\/} → use cicd:
       https://cicd.nint.hk/generic-webhook-trigger/invoke?token=sql-buddy-afk
       Content-type JSON, trigger = Issues
EOF

c "6. Park review job"
cat <<EOF
  Disable Jenkins job sql-buddy-review in the UI (do not delete).
  Keep Jenkinsfile + ci/review_post.py + Dockerfile in git.
  See docs/adr/0005-afk-consumer-park-review.md
EOF

c "7. Laptop dry-run (optional)"
cat <<EOF
  cd afk
  npm install
  cp .sandcastle/.env.example .sandcastle/.env   # fill ZAI + token
  npm run sandbox:build
  AFK_ISSUE=N npm run afk:dry    # prompt only
  AFK_ISSUE=N npm run afk        # full loop (needs Docker + tea + git push rights)
EOF

c "Done — complete any [ ] items above, then manual AFK_ISSUE build."
