#!/usr/bin/env python3
"""Post the Review agent's verdict to a Gitea pull request.

Reads the JSON verdict emitted by pi (the Review agent), derives the verdict
label from finding tiers, renders a bannered comment, and posts it + the label
to the Gitea API.

The Gitea token used here is scoped to comment + label only (no push). It stays
on the Jenkins side and never enters the pi container. See
docs/adr/0001-*.md for the security rationale.

Usage (from the Jenkinsfile post-review stage):

    python ci/review_post.py \\
        --gitea-url  https://git.sh.nint.com \\
        --repo       ying.yuxiang/sql-buddy \\
        --pr         42 \\
        --build-url  "$BUILD_URL" \\
        --token      "$GITEA_TOKEN" \\
        verdict.json            # pi's JSON output file (positional arg)

    # On failure (bad JSON / crash), use --error to post a fallback:
    python ci/review_post.py \\
        --gitea-url ... --repo ... --pr ... --build-url ... --token ... \\
        --error

Exit codes: 0 = posted successfully, 1 = error (Jenkins build goes red).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

VERDICT_LABELS = ("agent:approved", "needs-work", "good-attempt", "review-error")

BANNER = (
    "> \u2699\ufe0f **Automated Review pass** "
    "(Review agent / pi, posted via the maintainer's scoped token)"
)


# --------------------------------------------------------------------------- #
# Verdict logic (deterministic — matches AGENTS.md)
# --------------------------------------------------------------------------- #

def derive_label(findings: list[dict]) -> str:
    """Map finding tiers to a verdict label per the locked Q8 rule."""
    if any(f.get("tier") == 1 for f in findings):
        return "needs-work"
    if any(f.get("tier") == 2 for f in findings):
        return "good-attempt"
    return "agent:approved"


def render_comment(verdict: dict, label: str, build_url: str) -> str:
    """Render the bannered markdown comment from the verdict JSON."""
    lines = [BANNER + f" \u2014 verdict: `{label}`", ""]

    summary = verdict.get("summary", "").strip()
    if summary:
        lines += [summary, ""]

    findings = verdict.get("findings", [])
    tier1 = [f for f in findings if f.get("tier") == 1]
    tier2 = [f for f in findings if f.get("tier") == 2]

    if tier1:
        lines.append("**Blocking (Tier 1):**")
        for f in tier1:
            lines.append(f"- `{_loc(f)}` \u2014 {f.get('message', '')}")
        lines.append("")

    if tier2:
        lines.append("**Minor (Tier 2):**")
        for f in tier2:
            lines.append(f"- `{_loc(f)}` \u2014 {f.get('message', '')}")
        lines.append("")

    if not findings:
        lines.append("No issues found.")
        lines.append("")

    if build_url:
        lines.append(f"_Jenkins build: {build_url}_")

    return "\n".join(lines)


def _loc(f: dict) -> str:
    path = f.get("path", "?")
    line = f.get("line", 0)
    return f"{path}:{line}" if line else path


# --------------------------------------------------------------------------- #
# Gitea API (stdlib urllib — no external deps)
# --------------------------------------------------------------------------- #

def _api(
    method: str,
    gitea_url: str,
    repo: str,
    path: str,
    token: str,
    body: dict | None = None,
) -> dict | None:
    url = f"{gitea_url.rstrip('/')}/api/v1/repos/{repo}/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        print(f"Gitea API error {e.code} for {method} {url}: {e.reason}", file=sys.stderr)
        raise
    except urllib.error.URLError as e:
        print(f"Gitea API connection error for {method} {url}: {e.reason}", file=sys.stderr)
        raise


def post_comment(gitea_url: str, repo: str, pr: str, token: str, body: str) -> None:
    _api("POST", gitea_url, repo, f"issues/{pr}/comments", token, {"body": body})


def replace_verdict_labels(
    gitea_url: str, repo: str, pr: str, token: str, new_label: str
) -> None:
    """Remove any existing verdict labels, then add the new one."""
    for label in VERDICT_LABELS:
        if label == new_label:
            continue
        try:
            _api(
                "DELETE",
                gitea_url,
                repo,
                urllib.parse.quote(f"issues/{pr}/labels/{label}", safe="/"),
                token,
            )
        except urllib.error.HTTPError:
            pass  # label not present — fine
    _api(
        "POST",
        gitea_url,
        repo,
        f"issues/{pr}/labels",
        token,
        {"labels": [new_label]},
    )


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gitea-url", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True)
    p.add_argument("--build-url", default="")
    p.add_argument("--token", required=True)
    p.add_argument("--error", action="store_true", help="Post a failure fallback.")
    p.add_argument("verdict_file", nargs="?", help="pi's JSON output file")
    args = p.parse_args()

    if args.error:
        body = (
            f"{BANNER}\n\n"
            f"The Review pass could not complete (crash, timeout, or malformed "
            f"output). A human should investigate and re-trigger.\n\n"
            f"_Jenkins build: {args.build_url}_"
        )
        post_comment(args.gitea_url, args.repo, args.pr, args.token, body)
        replace_verdict_labels(args.gitea_url, args.repo, args.pr, args.token, "review-error")
        print("Posted review-error fallback.")
        return 0

    if not args.verdict_file:
        print("verdict_file is required unless --error is given", file=sys.stderr)
        return 1

    try:
        with open(args.verdict_file) as f:
            raw = f.read()
        # Pi sometimes emits reasoning prose (or markdown fences) around the
        # JSON despite the "output ONLY JSON" prompt — especially with
        # --thinking medium. Try a direct parse first (fast path for clean
        # output), then scan for a valid verdict using JSONDecoder.raw_decode.
        #
        # We do NOT use a greedy regex like re.search(r'\{.*\}', ...) — that
        # matches from the first '{' to the last '}', so an example JSON
        # object in pi's reasoning prose would swallow the real verdict and
        # produce invalid combined JSON. raw_decode correctly handles nested
        # braces and stops at the end of the first valid JSON value; combined
        # with shape validation (must have 'summary' + 'findings'), this
        # skips example fragments and finds the actual verdict.
        try:
            verdict = json.loads(raw)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            verdict = None
            for i, ch in enumerate(raw):
                if ch != '{':
                    continue
                try:
                    candidate, _ = decoder.raw_decode(raw[i:])
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) \
                        and 'summary' in candidate \
                        and 'findings' in candidate:
                    verdict = candidate
                    break
            if verdict is None:
                raise json.JSONDecodeError(
                    "no valid verdict object found in output", raw, 0)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not parse verdict JSON: {e}", file=sys.stderr)
        body = (
            f"{BANNER}\n\n"
            f"The Review agent produced output that could not be parsed as JSON. "
            f"A human should investigate.\n\n"
            f"_Jenkins build: {args.build_url}_"
        )
        post_comment(args.gitea_url, args.repo, args.pr, args.token, body)
        replace_verdict_labels(args.gitea_url, args.repo, args.pr, args.token, "review-error")
        return 0

    findings = verdict.get("findings", [])
    label = derive_label(findings)
    comment = render_comment(verdict, label, args.build_url)

    post_comment(args.gitea_url, args.repo, args.pr, args.token, comment)
    replace_verdict_labels(args.gitea_url, args.repo, args.pr, args.token, label)
    print(f"Posted review: {label} ({len(findings)} findings).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
