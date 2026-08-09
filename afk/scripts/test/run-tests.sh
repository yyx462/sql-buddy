#!/usr/bin/env bash
# Seam tests: tea harness + claim FSM with fake tea on PATH.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export AFK_ROOT="$ROOT"
export AFK_TEA_CONFIG_HOME="${ROOT}/.tea-config-test"
export AFK_REPO="ying.yuxiang/sql-buddy"
export AFK_LABEL="ready-for-agent"
export AFK_IN_PROGRESS_LABEL="afk:in-progress"
export AFK_BASE_BRANCH="master"
export AFK_TEA_LOGIN="sql-buddy-afk"
export AFK_GITEA_TOKEN="test-token-not-real"
rm -rf "${AFK_TEA_CONFIG_HOME}"
mkdir -p "${AFK_TEA_CONFIG_HOME}/tea"

FAKE_BIN="$(mktemp -d)"
trap 'rm -rf "${FAKE_BIN}" "${AFK_TEA_CONFIG_HOME}"' EXIT

# Fake tea: records argv to a log; returns canned JSON for reads.
LOG="${FAKE_BIN}/tea.log"
cat >"${FAKE_BIN}/tea" <<'EOF'
#!/usr/bin/env bash
echo "tea $*" >>"${TEA_LOG}"
# strip global flags for simple matching
args=("$@")
# ensure-login path uses: tea logins add ...
if [[ "$*" == *"logins add"* ]] || [[ "$*" == *"logins delete"* ]]; then
  exit 0
fi
if [[ "$*" == *"issues "* ]] && [[ "$*" != *"edit"* ]] && [[ "$*" != *"list"* ]]; then
  # tea issues N --output json
  cat <<'JSON'
{"index":7,"title":"docs fix","body":"Acceptance: fix typo","url":"https://git.example/i/7","state":"open","labels":["ready-for-agent"]}
JSON
  exit 0
fi
if [[ "$*" == *"issues list"* ]]; then
  cat <<'JSON'
[{"index":7,"title":"docs fix","body":"Acceptance: fix typo","url":"https://git.example/i/7","state":"open","labels":["ready-for-agent"]}]
JSON
  exit 0
fi
if [[ "$*" == *"issues edit"* ]]; then
  exit 0
fi
if [[ "$*" == *"comments add"* ]] || [[ "$*" == *"comment"* ]]; then
  exit 0
fi
if [[ "$*" == *"pulls list"* ]]; then
  echo '[]'
  exit 0
fi
if [[ "$*" == *"pulls create"* ]]; then
  echo '{"number":99,"html_url":"https://git.example/p/99"}'
  exit 0
fi
if [[ "$*" == *"labels"* ]]; then
  echo '[]'
  exit 0
fi
exit 0
EOF
chmod +x "${FAKE_BIN}/tea"
export PATH="${FAKE_BIN}:$PATH"
export TEA_LOG="$LOG"

TEA_SH="${ROOT}/scripts/tea.sh"
chmod +x "${TEA_SH}" "${ROOT}/scripts/open-pr.sh" "${ROOT}/scripts/build-sandbox.sh" 2>/dev/null || true

pass=0
fail=0
assert() {
  local name="$1" cond="$2"
  if eval "${cond}"; then
    echo "PASS  ${name}"
    pass=$((pass + 1))
  else
    echo "FAIL  ${name}"
    fail=$((fail + 1))
  fi
}

# --- ensure-login does not leave token in log ---
: >"$LOG"
bash "${TEA_SH}" ensure-login
assert "ensure-login runs" "grep -q 'logins add' '$LOG'"
assert "token not in tea.log" "! grep -q 'test-token-not-real' '$LOG'"

# --- issue-view JSON ---
out="$(bash "${TEA_SH}" issue-view 7)"
assert "issue-view json has index" "echo '$out' | grep -q '\"index\":7'"

# --- claim edits labels ---
: >"$LOG"
bash "${TEA_SH}" claim 7
assert "claim removes ready-for-agent" "grep -q 'remove-labels ready-for-agent' '$LOG' || grep -q \"remove-labels ${AFK_LABEL}\" '$LOG'"
assert "claim adds afk:in-progress" "grep -q 'add-labels afk:in-progress' '$LOG' || grep -q \"add-labels ${AFK_IN_PROGRESS_LABEL}\" '$LOG'"
assert "claim comments" "grep -q 'comments add' '$LOG' || grep -q 'comment' '$LOG'"

# --- finish-claim ---
: >"$LOG"
bash "${TEA_SH}" finish-claim 7
assert "finish-claim removes progress" "grep -q 'remove-labels' '$LOG'"

# --- pr-exists empty ---
: >"$LOG"
if bash "${TEA_SH}" pr-exists-for-branch afk/issue-7; then
  exists=yes
else
  exists=no
fi
assert "pr-exists no" "[[ '$exists' == 'no' ]]"

# --- dry-run main fills prompt (no docker) ---
if command -v npx >/dev/null 2>&1 && [[ -d "${ROOT}/node_modules" ]]; then
  export AFK_ISSUE=7
  export AFK_DRY_RUN=1
  export AFK_GITEA_TOKEN=test-token-not-real
  cd "${ROOT}"
  if npx tsx .sandcastle/main.ts >/tmp/afk-dry.out 2>&1; then
    assert "dry-run writes prompt" "[[ -f '${ROOT}/.sandcastle/.prompt.filled.md' ]]"
    assert "dry-run prompt has issue" "grep -q '#7' '${ROOT}/.sandcastle/.prompt.filled.md'"
  else
    echo "---- dry-run output ----"
    cat /tmp/afk-dry.out || true
    assert "dry-run main" "false"
  fi
else
  echo "SKIP  dry-run main (npm install first)"
fi

echo
echo "Results: ${pass} passed, ${fail} failed"
[[ "${fail}" -eq 0 ]]
