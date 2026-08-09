#!/usr/bin/env bash
# Careful tea harness for AFK. Never echo tokens. Never pass token on argv
# after ensure-login (tea stores it under a private config dir).
#
# Commands:
#   tea.sh ensure-login
#   tea.sh issue-view <n>
#   tea.sh issue-list-ready
#   tea.sh claim <n>          # ready-for-agent → afk:in-progress + comment
#   tea.sh release-claim <n>  # on failure: drop in-progress (optional re-add ready)
#   tea.sh finish-claim <n>   # after PR: drop ready + in-progress
#   tea.sh comment <n> <body>
#   tea.sh pr-create <head> <title> <body-file>
#   tea.sh pr-exists-for-branch <head>
#   tea.sh labels-ensure       # create ready-for-agent + afk:in-progress if missing
set -euo pipefail

REPO="${AFK_REPO:-ying.yuxiang/sql-buddy}"
GITEA_URL="${AFK_GITEA_URL:-https://git.sh.nint.com}"
LOGIN_NAME="${AFK_TEA_LOGIN:-sql-buddy-afk}"
READY_LABEL="${AFK_LABEL:-ready-for-agent}"
PROGRESS_LABEL="${AFK_IN_PROGRESS_LABEL:-afk:in-progress}"
BASE_BRANCH="${AFK_BASE_BRANCH:-master}"

# Isolate tea config so we never touch the operator's personal logins and so
# Jenkins can use a workspace-local dir.
AFK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export XDG_CONFIG_HOME="${AFK_TEA_CONFIG_HOME:-${AFK_ROOT}/.tea-config}"
mkdir -p "${XDG_CONFIG_HOME}/tea"

die() { echo "tea.sh: $*" >&2; exit 1; }

require_tea() {
  command -v tea >/dev/null 2>&1 || die "tea not on PATH (install gitea tea CLI)"
}

# Read token from file or env. Never print it.
read_token() {
  if [[ -n "${AFK_GITEA_TOKEN_FILE:-}" && -f "${AFK_GITEA_TOKEN_FILE}" ]]; then
    tr -d '[:space:]' <"${AFK_GITEA_TOKEN_FILE}"
    return
  fi
  if [[ -n "${AFK_GITEA_TOKEN:-}" ]]; then
    printf '%s' "${AFK_GITEA_TOKEN}"
    return
  fi
  if [[ -n "${GITEA_TOKEN:-}" ]]; then
    printf '%s' "${GITEA_TOKEN}"
    return
  fi
  die "set AFK_GITEA_TOKEN_FILE or AFK_GITEA_TOKEN (scoped AFK token, not review token)"
}

tea_cmd() {
  require_tea
  # --login + --repo always — no accidental wrong-forge ops
  tea --login "${LOGIN_NAME}" --repo "${REPO}" "$@"
}

cmd_ensure_login() {
  require_tea
  local token
  token="$(read_token)"
  # Remove stale login silently so token rotation works
  tea logins delete "${LOGIN_NAME}" >/dev/null 2>&1 || true
  # Token ONLY via env (GITEA_SERVER_TOKEN) — never --token on argv (ps/logs).
  {
    set +x
  } 2>/dev/null
  GITEA_SERVER_URL="${GITEA_URL}" \
    GITEA_SERVER_TOKEN="${token}" \
    tea logins add \
      --name "${LOGIN_NAME}" \
      --url "${GITEA_URL}" \
      --no-version-check \
      >/dev/null
  unset token GITEA_SERVER_TOKEN
  echo "tea.sh: login ${LOGIN_NAME} ok for ${REPO}"
}

cmd_issue_view() {
  local n="${1:?issue number}"
  tea_cmd issues "${n}" \
    --fields index,title,body,url,state,labels \
    --output json
}

cmd_issue_list_ready() {
  tea_cmd issues list \
    --state open \
    --labels "${READY_LABEL}" \
    --limit 5 \
    --fields index,title,body,url,state,labels \
    --output json
}

cmd_comment() {
  local n="${1:?issue}"
  local body="${2:?body}"
  tea_cmd comments add "${n}" --description "${body}" >/dev/null
  echo "tea.sh: commented on #${n}"
}

cmd_claim() {
  local n="${1:?issue}"
  tea_cmd issues edit "${n}" \
    --add-labels "${PROGRESS_LABEL}" \
    --remove-labels "${READY_LABEL}" \
    >/dev/null
  cmd_comment "${n}" "AFK started (in-progress). Branch will be \`afk/issue-${n}\`."
  echo "tea.sh: claimed #${n} (${READY_LABEL} → ${PROGRESS_LABEL})"
}

cmd_release_claim() {
  local n="${1:?issue}"
  tea_cmd issues edit "${n}" \
    --remove-labels "${PROGRESS_LABEL}" \
    --add-labels "${READY_LABEL}" \
    >/dev/null || true
  cmd_comment "${n}" "AFK run failed or produced no commits; re-queued as \`${READY_LABEL}\`." || true
  echo "tea.sh: released claim on #${n}"
}

cmd_finish_claim() {
  local n="${1:?issue}"
  tea_cmd issues edit "${n}" \
    --remove-labels "${PROGRESS_LABEL},${READY_LABEL}" \
    >/dev/null || true
  echo "tea.sh: finished claim on #${n}"
}

cmd_pr_create() {
  local head="${1:?head branch}"
  local title="${2:?title}"
  local body_file="${3:?body file}"
  [[ -f "${body_file}" ]] || die "body file missing: ${body_file}"
  local body
  body="$(cat "${body_file}")"
  tea_cmd pulls create \
    --head "${head}" \
    --base "${BASE_BRANCH}" \
    --title "${title}" \
    --description "${body}" \
    --output json
}

cmd_pr_exists_for_branch() {
  local head="${1:?head}"
  # tea list open PRs; match head branch name in JSON/text
  local out
  out="$(tea_cmd pulls list --state open --limit 50 --fields index,title,head,state --output json 2>/dev/null || echo '[]')"
  if echo "${out}" | grep -q "\"${head}\""; then
    echo "yes"
    return 0
  fi
  # Also try head field shapes like owner:branch
  if echo "${out}" | grep -q "${head}"; then
    echo "yes"
    return 0
  fi
  echo "no"
  return 1
}

cmd_labels_ensure() {
  local existing
  existing="$(tea_cmd labels list --output json 2>/dev/null || echo '[]')"
  if ! echo "${existing}" | grep -q "${READY_LABEL}"; then
    tea_cmd labels create --name "${READY_LABEL}" --color "0e8a16" \
      --description "Ready for AFK implement agent" >/dev/null \
      || echo "tea.sh: create label ${READY_LABEL} manually if missing" >&2
  fi
  if ! echo "${existing}" | grep -q "${PROGRESS_LABEL}"; then
    tea_cmd labels create --name "${PROGRESS_LABEL}" --color "fbca04" \
      --description "AFK run in progress" >/dev/null \
      || echo "tea.sh: create label ${PROGRESS_LABEL} manually if missing" >&2
  fi
  echo "tea.sh: labels ok"
}

usage() {
  sed -n '2,20p' "$0" | tr -d '#'
  exit 2
}

main() {
  local cmd="${1:-}"
  shift || true
  case "${cmd}" in
    ensure-login) cmd_ensure_login "$@" ;;
    issue-view) cmd_issue_view "$@" ;;
    issue-list-ready) cmd_issue_list_ready "$@" ;;
    claim) cmd_claim "$@" ;;
    release-claim) cmd_release_claim "$@" ;;
    finish-claim) cmd_finish_claim "$@" ;;
    comment) cmd_comment "$@" ;;
    pr-create) cmd_pr_create "$@" ;;
    pr-exists-for-branch) cmd_pr_exists_for_branch "$@" ;;
    labels-ensure) cmd_labels_ensure "$@" ;;
    ""|-h|--help) usage ;;
    *) die "unknown command: ${cmd}" ;;
  esac
}

main "$@"
