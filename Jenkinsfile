// sql-buddy review + sync pipeline — Jenkins job "sql-buddy-review" (cicd.nint.hk)
//
// Trigger: a Gitea "pull request" webhook hits the Generic Webhook Trigger
// plugin, filtered to opened / reopened / synchronized / ready_for_review /
// closed PRs. On the first four, the Review agent (pi) reads the diff + repo
// and emits a JSON verdict; this pipeline derives the label, renders a bannered
// comment, and posts both to the PR via the Gitea API. On `closed`+`merged`,
// review is skipped but the workspace is still synced (see post-merge sync
// below).
//
// Post-merge sync: the `post { always }` block fast-forwards $WORKSPACE to
// origin/master after EVERY trigger (review, skip, merge, or failure). This
// replaces the former sql-buddy-sync pipeline (Jenkinsfile.sync) — one
// pipeline, one workspace, no duplicate /data/www/algo/sql-buddy-sync folder.
// To migrate: delete the sql-buddy-sync Jenkins job + its webhook; this file
// is the single source of truth.
//
// Secrets (never in the repo) — both live as FILES on the web01 node, mirroring
// the amazon_alg Jenkinsfile doctrine: keep secrets out of the repo AND out of
// the Jenkins cred store (the system cred store needs Administer rights, which
// the maintainer doesn't have).
//   - Gitea token   : $GITEA_TOKEN_FILE, scoped to comment + label only (NO
//     push). Read into $GITEA_TOKEN at runtime.
//   - LLM API key   : $MINIMAX_KEY_FILE, read into $MINIMAX_KEY at runtime and
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
//   7. Put the MiniMax API key at $MINIMAX_KEY_FILE on web01 (chmod 600, owned
//      by the same user as $GITEA_TOKEN_FILE).

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
        GITEA_URL             = 'https://git.sh.nint.com'
        MAINTAINER            = 'ying.yuxiang'
        GITEA_TOKEN_FILE      = '/data/www/algo/deps/gitea-token'
        MINIMAX_KEY_FILE      = '/data/www/algo/deps/minimax-key'
        IMAGE                 = 'sql-buddy-review'
        MINIMAX_MODEL         = 'MiniMax-M3'
        // Python interpreter lives in the shared uv-managed install (mirrors
        // amazon_alg's Jenkinsfile). The agent's default sh PATH doesn't
        // include /usr/bin/python3, so we go through uv to find python.
        UV                    = '/data/www/algo/deps/uv'
        UV_CACHE_DIR          = '/data/www/algo/deps/uv-cache'
        UV_PYTHON_INSTALL_DIR = '/data/www/algo/deps/uv-python'
        REVIEW_PROMPT         = 'Review pull request #$PR_NUMBER. The unified diff is at /workspace/pr.diff — read it first. Then explore the repository as needed to evaluate each change against the rubric in AGENTS.md. Follow the pr-review skill: classify each finding into tier 1 (blocking) or tier 2 (minor). Output ONLY a JSON object: {"summary": "...", "findings": [{"tier": 1, "path": "...", "line": 0, "message": "..."}]}. If there are no findings, output {"summary": "...", "findings": []}. Output nothing except the JSON.'
    }

    triggers {
        GenericTrigger(
            token: 'sql-buddy-review',
            causeString: 'PR #$PR_NUMBER review',
            genericVariables: [
                [key: 'ACTION',    value: '$.action'],
                [key: 'PR_NUMBER', value: '$.number'],
                [key: 'DRAFT',     value: '$.pull_request.draft'],
                [key: 'MERGED',    value: '$.pull_request.merged'],
                [key: 'AUTHOR',    value: '$.pull_request.user.login'],
                [key: 'HEAD_SHA',  value: '$.pull_request.head.sha'],
                [key: 'BASE_SHA',  value: '$.pull_request.base.sha'],
                [key: 'REPO_FULL', value: '$.repository.full_name'],
            ],
            regexpFilterText: '$ACTION',
            regexpFilterExpression: '^(opened|reopened|synchronized|ready_for_review|closed)$',
            printContributedVariables: false,
            printPostContent: false,
        )
    }

    stages {

        stage('Skip check') {
            steps {
                // Decide whether to skip; if so, capture a reason string the
                // downstream `when` conditions read. We do NOT use `exit 0`
                // here — that returned 0 to Jenkins, which treated it as
                // "this step succeeded" and continued to the next stage.
                // On a manual Build Now (no $PR_NUMBER), the Checkout PR
                // stage then ran with empty $HEAD_SHA and crashed.
                //
                // The shell below writes a skip-reason string to stdout
                // when skipping, or empty stdout when proceeding. The
                // pipeline reads it via `returnStdout: true` into
                // env.SKIP_REASON, and downstream stages are gated on
                // `!env.SKIP_REASON.trim()`.
                script {
                    env.SKIP_REASON = sh(
                        returnStdout: true,
                        script: '''
                            set -e
                            if [ -z "${PR_NUMBER:-}" ]; then
                                echo "manual build (no PR payload)"
                                exit 0
                            fi
                            # closed action = a PR was merged (or closed without
                            # merge). Either way there's nothing to review — the
                            # stages below are skipped via the returned reason.
                            # The post { always } sync still runs, so a merge
                            # fast-forwards the workspace to the new master.
                            if [ "$ACTION" = "closed" ]; then
                                if [ "$MERGED" = "true" ]; then
                                    echo "PR merged, sync only (review skipped)"
                                else
                                    echo "PR closed without merge"
                                fi
                                exit 0
                            fi
                            # TEMPORARY: maintainer-skip disabled so the
                            # review pipeline runs on the maintainer's own
                            # PRs too. This is to live-smoke-test the review
                            # pipeline end-to-end without a coworker PR.
                            # Restore this block once smoke is complete:
                            #   if [ "$AUTHOR" = "$MAINTAINER" ]; then
                            #       echo "PR authored by maintainer ($MAINTAINER)"
                            #       exit 0
                            #   fi
                            if [ "$DRAFT" = "true" ]; then
                                echo "draft PR"
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
                                echo "PR has skip-review label"
                                exit 0
                            fi
                            # Empty stdout — proceeds to review.
                        '''
                    ).trim()
                    if (env.SKIP_REASON) {
                        echo "skip: ${env.SKIP_REASON}"
                    } else if (env.PR_NUMBER) {
                        echo "proceed: reviewing PR #${env.PR_NUMBER} by ${env.AUTHOR}"
                    }
                }
            }
        }

        stage('Checkout PR') {
            when { expression { return !env.SKIP_REASON?.trim() } }
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
            when { expression { return !env.SKIP_REASON?.trim() } }
            steps {
                // pi runs read-only (--tools read,grep,find,ls), non-interactive,
                // with the repo mounted at /workspace. Only the LLM key enters
                // the container; the Gitea token does NOT. The key is read from
                // a file on web01 (same pattern as $GITEA_TOKEN_FILE, and as
                // amazon_alg) — see docs/adr/0001-*.md for the security rationale.
                //
                // SECRET-LEAK GUARD: do NOT pass the key as `-e NAME=<value>` —
                // Jenkins sh tracing (set -x) would echo the literal value into
                // the build log. Instead export under set +x and use `--env NAME`
                // (no =value) so docker inherits it from the environment; the
                // traced docker CLI then shows only the variable NAME. This is
                // the only safe shape once we left withCredentials (which masked
                // automatically) for the file-on-disk pattern.
                sh '''
                set -e
                { set +x; } 2>/dev/null
                export MINIMAX_CN_API_KEY="$(cat "$MINIMAX_KEY_FILE")"
                { set -x; } 2>/dev/null
                docker run --rm \
                    -v "$WORKSPACE:/workspace" \
                    --env MINIMAX_CN_API_KEY \
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

        stage('Post review') {
            when { expression { return !env.SKIP_REASON?.trim() } }
            steps {
                sh '''
                set +e
                { set +x; } 2>/dev/null
                export GITEA_TOKEN="$(cat "$GITEA_TOKEN_FILE")"
                { set -x; } 2>/dev/null
                # Run ci/review_post.py via the uv-managed python install
                # (agent's default sh PATH doesn't include /usr/bin/python3).
                # --no-project so uv ignores sql-buddy's own pyproject.toml.
                "$UV" run --no-project python ci/review_post.py \
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
        always {
            // Fast-forward $WORKSPACE to origin/master after EVERY trigger —
            // review, skip, merge, or failure. Replaces the former
            // sql-buddy-sync pipeline: one pipeline, one workspace, no
            // duplicate /data/www/algo/sql-buddy-sync folder. Best-effort: a
            // sync failure must NOT mask the real build result.
            //
            // Retry the fetch because Gitea's merge commit may not be
            // replicated to all backends the instant the webhook fires.
            sh '''
                set +e
                cd "$WORKSPACE" 2>/dev/null || exit 0
                [ -d .git ] || exit 0
                git fetch origin master 2>/tmp/sqlbuddy-sync.err || {
                    echo "sync: fetch failed, retrying in 5s"
                    sleep 5
                    git fetch origin master 2>>/tmp/sqlbuddy-sync.err || {
                        echo "sync: fetch failed after retry (non-fatal):"
                        cat /tmp/sqlbuddy-sync.err
                        exit 0
                    }
                }
                git checkout -B master origin/master 2>/dev/null
                git reset --hard origin/master 2>/dev/null
                git clean -fd 2>/dev/null
                echo "sync: workspace at $(git log --oneline -1)"
            '''
        }
        failure {
            // Something blew up (timeout, container crash, bad JSON, API error).
            // Post a review-error fallback so the PR isn't left silent.
            sh '''
                set +e
                { set +x; } 2>/dev/null
                export GITEA_TOKEN="$(cat "$GITEA_TOKEN_FILE" 2>/dev/null || true)"
                { set -x; } 2>/dev/null
                if [ -n "${PR_NUMBER:-}" ] && [ -n "${GITEA_TOKEN:-}" ]; then
                    "$UV" run --no-project python ci/review_post.py \
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
