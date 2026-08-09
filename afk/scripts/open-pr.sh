#!/usr/bin/env bash
# Wrapper opens Gitea PR after agent commits (human merges).
# Usage: open-pr.sh <issue-number> <branch> <title>
set -euo pipefail

ISSUE="${1:?issue number}"
BRANCH="${2:?branch}"
TITLE="${3:?title}"

AFK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEA_SH="${AFK_ROOT}/scripts/tea.sh"
BASE="${AFK_BASE_BRANCH:-master}"
REPO_ROOT="${AFK_CWD:-$(cd "${AFK_ROOT}/.." && pwd)}"

cd "${REPO_ROOT}"

echo "open-pr: base=${BASE} head=${BRANCH} issue=#${ISSUE}"

# Idempotent: PR already open for this branch?
if [[ "$("${TEA_SH}" pr-exists-for-branch "${BRANCH}" || true)" == "yes" ]]; then
  echo "open-pr: PR already exists for ${BRANCH} — skip create"
  "${TEA_SH}" finish-claim "${ISSUE}" || true
  exit 0
fi

# Bot identity for any amend-free push metadata (commits already made in sandbox)
export GIT_AUTHOR_NAME="${AFK_GIT_AUTHOR_NAME:-sql-buddy-afk}"
export GIT_AUTHOR_EMAIL="${AFK_GIT_AUTHOR_EMAIL:-sql-buddy-afk@local}"
export GIT_COMMITTER_NAME="${GIT_AUTHOR_NAME}"
export GIT_COMMITTER_EMAIL="${GIT_AUTHOR_EMAIL}"

git push -u origin "${BRANCH}"

ahead="$(git rev-list --count "origin/${BASE}..${BRANCH}" 2>/dev/null \
  || git rev-list --count "${BASE}..${BRANCH}" 2>/dev/null \
  || echo 0)"
if [[ "${ahead}" == "0" ]]; then
  echo "open-pr: no commits on ${BRANCH} ahead of ${BASE} — skip PR" >&2
  exit 2
fi

body_file="$(mktemp)"
trap 'rm -f "${body_file}"' EXIT
cat >"${body_file}" <<EOF
Automated AFK PR for #${ISSUE}.

Fixes #${ISSUE}

## Notes
- Agent committed on \`${BRANCH}\`; host wrapper opened this PR via tea.
- Human merges after review.
- Review agent job is parked; please self-check Locked-layer / secrets rules in AGENTS.md.
EOF

"${TEA_SH}" pr-create "${BRANCH}" "afk: ${TITLE}" "${body_file}"
"${TEA_SH}" finish-claim "${ISSUE}" || true
echo "open-pr: done"
