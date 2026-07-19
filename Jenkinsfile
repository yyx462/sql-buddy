// sql-buddy review pipeline — Jenkins job "sql-buddy-review" (cicd.nint.hk)
//
// Trigger: a Gitea "pull request" webhook hits the Generic Webhook Trigger
// plugin, filtered to opened / reopened / synchronized / ready_for_review PRs.
// The Review agent (pi) reads the diff + repo and emits a JSON verdict; this
// pipeline derives the label, renders a bannered comment, and posts both
// to the PR via the Gitea API.
//
// Secrets (never in the repo):
//   - Gitea token   : a FILE on the web01 node ($GITEA_TOKEN_FILE), scoped to
//     comment + label only (NO push). Same file-on-disk pattern as the
//     algo-task-deploy job. Read into $GITEA_TOKEN at runtime.
//   - LLM API key   : a Jenkins Secret-text credential ('minimax-auth-token'),
//     injected as $MINIMAX_CN_API_KEY into the pi container only.
//     We use pi's built-in 'minimax-cn' provider, whose base URL is hardcoded
//     to https://api.minimaxi.com/anthropic inside pi — no env override needed
//     (and none is honored: pi ignores MINIMAX_BASE_URL for the minimax/providers).
//
// One-time job setup (UI):
//   1. "Generic Webhook Trigger" plugin (already installed for algo-task-deploy).
//   2. New Item → Pipeline; Definition = "Pipeline script from SCM"; repo URL =
//      git@git.sh.nint.com:ying.yuxiang/sql-buddy.git ; the 'sea' git credential;
//      branch master; Script path "Jenkinsfile".
//      Node restriction = agent{label'web01'} below.
//   3. Save + run once manually so Jenkins registers the triggers{} block.
//   4. Gitea repo → Settings → Webhooks → URL
//      https://cicd.nint.hk/generic-webhook-trigger/invoke?token=sql-buddy-review
//      content-type application/json, trigger = Pull requests.
//   5. Create Gitea labels: agent:approved, needs-work, good-attempt,
//      skip-review, review-error.
//   6. Put the scoped Gitea token at $GITEA_TOKEN_FILE on web01 (chmod 600).
//   7. Add the Jenkins credential 'minimax-auth-token' (Secret text) with the MiniMax API key.

pipeline {
    agent {
        node {
            label 'web01'
            customWorkspace '/data/www/algo/sql-buddy-review'
        }
    }

    options {
        disableConcurrentBuilds()                          // 1 review at a time
        buildDiscarder(logRotator(numToKeepStr: '50'))
        timeout(time: 10, unit: 'MINUTES')                 // hard bound on pi
        timestamps()
    }

    environment {
        GITEA_URL        = 'https://git.sh.nint.com'
        MAINTAINER       = 'ying.yuxiang'
        GITEA_TOKEN_FILE = '/data/www/algo/deps/gitea-token'
        IMAGE            = 'sql-buddy-review'
        MINIMAX_MODEL    = 'MiniMax-M3'
        REVIEW_PROMPT    = 'Review pull request #$PR_NUMBER. The unified diff is at /workspace/pr.diff — read it first. Then explore the repository as needed to evaluate each change against the rubric in AGENTS.md. Follow the pr-review skill: classify each finding into tier 1 (blocking) or tier 2 (minor). Output ONLY a JSON object: {"summary": "...", "findings": [{"tier": 1, "path": "...", "line": 0, "message": "..."}]}. If there are no findings, output {"summary": "...", "findings": []}. Output nothing except the JSON.'
    }

    triggers {
        GenericTrigger(
            token: 'sql-buddy-review',
            causeString: 'PR #$PR_NUMBER review',
            genericVariables: [
                [key: 'ACTION',    value: '$.action'],
                [key: 'PR_NUMBER', value: '$.number'],
                [key: 'DRAFT',     value: '$.pull_request.draft'],
                [key: 'AUTHOR',    value: '$.pull_request.user.login'],
                [key: 'HEAD_SHA',  value: '$.pull_request.head.sha'],
                [key: 'BASE_SHA',  value: '$.pull_request.base.sha'],
                [key: 'REPO_FULL', value: '$.repository.full_name'],
            ],
            regexpFilterText: '$ACTION',
            regexpFilterExpression: '^(opened|reopened|synchronized|ready_for_review)$',
            printContributedVariables: false,
            printPostContent: false,
        )
    }

    stages {

        stage('Skip check') {
            steps {
                // Manual Build Now (no PR payload) → clean skip, not an error.
                // Real values arrive only from a PR webhook.
                sh '''
                set -e
                if [ -z "${PR_NUMBER:-}" ]; then
                    echo "manual build (no PR payload) -> skip"
                    exit 0
                fi
                if [ "$AUTHOR" = "$MAINTAINER" ]; then
                    echo "skip: PR authored by maintainer ($MAINTAINER)"
                    exit 0
                fi
                if [ "$DRAFT" = "true" ]; then
                    echo "skip: draft PR"
                    exit 0
                fi
                # Check for skip-review label via Gitea API.
                { set +x; } 2>/dev/null
                export GITEA_TOKEN="$(cat "$GITEA_TOKEN_FILE" 2>/dev/null || true)"
                LABELS=$(curl -sf \
                    -H "Authorization: token $GITEA_TOKEN" \
                    "$GITEA_URL/api/v1/repos/$REPO_FULL/issues/$PR_NUMBER/labels" \
                    2>/dev/null || echo '[]')
                { set -x; } 2>/dev/null
                if echo "$LABELS" | grep -q '"skip-review"'; then
                    echo "skip: PR has skip-review label"
                    exit 0
                fi
                echo "proceed: reviewing PR #$PR_NUMBER by $AUTHOR"
                '''
            }
        }

        stage('Checkout PR') {
            steps {
                sh '''
                set -e
                { set +x; } 2>/dev/null
                export GITEA_TOKEN="$(cat "$GITEA_TOKEN_FILE")"
                REPO_URL="https://x-access-token:${GITEA_TOKEN}@git.sh.nint.com/ying.yuxiang/sql-buddy.git"
                { set -x; } 2>/dev/null
                if [ ! -d .git ]; then
                    git clone "$REPO_URL" .
                fi
                git fetch origin "$HEAD_SHA"
                git checkout "$HEAD_SHA" -f
                git log --oneline -1
                # Build the diff for the Review agent to read.
                git diff "$BASE_SHA...$HEAD_SHA" > pr.diff
                echo "diff: $(wc -l < pr.diff) lines"
                '''
            }
        }

        stage('Review') {
            steps {
                // pi runs read-only (--tools read,grep,find,ls), non-interactive,
                // with the repo mounted at /workspace. Only the LLM key enters
                // the container; the Gitea token does NOT.
                withCredentials([string(credentialsId: 'minimax-auth-token', variable: 'MINIMAX_KEY')]) {
                    sh '''
                    set -e
                    docker run --rm \
                        -v "$WORKSPACE:/workspace" \
                        -e MINIMAX_CN_API_KEY="$MINIMAX_KEY" \
                        -e PI_OFFLINE=1 \
                        "$IMAGE" \
                        --provider minimax-cn \
                        --model "$MINIMAX_MODEL" \
                        --tools read,grep,find,ls \
                        --no-session \
                        --thinking medium \
                        -a \
                        -p "$REVIEW_PROMPT" \
                        > verdict.json 2>pi-stderr.log
                    echo "=== pi output ==="
                    head -c 2000 verdict.json
                    echo
                    echo "=== pi stderr (last 20 lines) ==="
                    tail -20 pi-stderr.log || true
                    '''
                }
            }
        }

        stage('Post review') {
            steps {
                sh '''
                set +e
                { set +x; } 2>/dev/null
                export GITEA_TOKEN="$(cat "$GITEA_TOKEN_FILE")"
                { set -x; } 2>/dev/null
                python3 ci/review_post.py \
                    --gitea-url "$GITEA_URL" \
                    --repo "$REPO_FULL" \
                    --pr "$PR_NUMBER" \
                    --build-url "$BUILD_URL" \
                    --token "$GITEA_TOKEN" \
                    verdict.json
                '''
            }
        }
    }

    post {
        failure {
            // Something blew up (timeout, container crash, bad JSON, API error).
            // Post a review-error fallback so the PR isn't left silent.
            sh '''
                set +e
                { set +x; } 2>/dev/null
                export GITEA_TOKEN="$(cat "$GITEA_TOKEN_FILE" 2>/dev/null || true)"
                { set -x; } 2>/dev/null
                if [ -n "${PR_NUMBER:-}" ] && [ -n "${GITEA_TOKEN:-}" ]; then
                    python3 ci/review_post.py \
                        --gitea-url "$GITEA_URL" \
                        --repo "${REPO_FULL:-ying.yuxiang/sql-buddy}" \
                        --pr "$PR_NUMBER" \
                        --build-url "$BUILD_URL" \
                        --token "$GITEA_TOKEN" \
                        --error
                fi
            '''
        }
    }
}
