#!/usr/bin/env python3
"""Check if a Gitea pull request is actually merged — authoritative, no guessing.

The agent sometimes hallucinates "this PR is not merged" when it actually is
(e.g. the local `master` is stale, or the agent misread the Gitea UI text).
This script queries the Gitea API directly and prints unambiguous output:

    PR #4: MERGED into ying.yuxiang/sql-buddy at 2026-07-19T21:00:00Z
        merge_commit: deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
        head_sha:      c55410e3af2b70527c77025d0314d91a7ad17d21
    (exit 0)

or:

    PR #4: OPEN (not merged) — state=open, merged=false
    (exit 1)

Exit codes (so the agent can branch on `$?`):
    0 = merged
    1 = not merged (open or closed-without-merge)
    2 = could not determine (API error, bad PR number, auth failure)

Usage:

    GITEA_TOKEN=... python3 pr_merged.py <PR_NUMBER>
    GITEA_TOKEN=... python3 pr_merged.py 4

If `GITEA_TOKEN` is unset or the call 401s, the script falls back to an
unauthenticated GET (Gitea allows read access on public repos). If the repo
is private and the token is missing/bad, exit 2 with a clear message.

This script is read-only: it issues a single GET and prints one line + the
PR's merge metadata. It never mutates anything.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

INSTANCE = "https://git.sh.nint.com"
REPO = "ying.yuxiang/sql-buddy"


def _get(url: str, token: str | None) -> tuple[int, bytes]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        print(f"network error: {e.reason}", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("usage: pr_merged.py <PR_NUMBER>", file=sys.stderr)
        return 2

    pr_number = int(sys.argv[1])
    token = os.environ.get("GITEA_TOKEN") or None
    url = f"{INSTANCE}/api/v1/repos/{REPO}/pulls/{pr_number}"

    status, body = _get(url, token)
    if status == 401 and token:
        # Fall back to unauthenticated read for public repos.
        status, body = _get(url, None)
    if status == 404:
        print(f"PR #{pr_number}: not found at {url}", file=sys.stderr)
        return 2
    if status >= 400:
        print(f"PR #{pr_number}: API error HTTP {status}: {body[:200]!r}", file=sys.stderr)
        return 2

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        print(f"PR #{pr_number}: bad JSON from API: {e}", file=sys.stderr)
        return 2

    state = data.get("state")
    merged = bool(data.get("merged"))
    merged_at = data.get("merged_at")
    merge_commit = data.get("merge_commit_sha")
    head_sha = (data.get("head") or {}).get("sha")

    if merged and merged_at:
        print(f"PR #{pr_number}: MERGED into {REPO} at {merged_at}")
        if merge_commit:
            print(f"    merge_commit: {merge_commit}")
        if head_sha:
            print(f"    head_sha:      {head_sha}")
        return 0

    print(f"PR #{pr_number}: NOT MERGED — state={state}, merged={merged}")
    if state == "closed" and not merged:
        print("    (PR was closed without merging)")
    return 1


if __name__ == "__main__":
    sys.exit(main())