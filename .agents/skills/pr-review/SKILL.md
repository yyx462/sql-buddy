# PR Review

Review a pull request against the rubric in AGENTS.md and emit a structured
JSON verdict. The pipeline (not the agent) derives the verdict label from your
finding tiers.

## When to use

Invoked automatically by the Jenkins review pipeline on every non-skipped
pull request. You are the Review agent; your job is analysis only — you cannot
modify files, push, or post comments directly.

## Procedure

1. **Read the diff.** The unified diff is at `/workspace/pr.diff`. Read it to
   understand what the PR changes.

2. **Explore the repo.** Use `read`, `grep`, `find`, `ls` to examine the
   surrounding code. You MUST look beyond the diff to evaluate the read-only
   invariant — the Locked layer lives in `db_query.py`, not necessarily in the
   diff. Read `UBIQUITOUS_LANGUAGE.md` for the domain invariants.

3. **Classify each finding** into a tier per AGENTS.md:
   - **Tier 1 (blocking):** read-only invariant violation, secret/credential
     exposure, correctness bug.
   - **Tier 2 (minor):** ubiquitous-language drift, API inconsistency, naming,
     style, obvious perf regression.
   - **Below the bar:** trivial nitpicks — do NOT report these.

4. **Write the summary** as one paragraph assessing the PR overall. Be direct
   and constructive. Uniform tone for all authors.

5. **Emit the JSON verdict** (see Output below). Output ONLY the JSON — no
   prose before or after, no markdown code fences.

## Output

```json
{
  "summary": "One-paragraph overall assessment of the PR.",
  "findings": [
    {"tier": 1, "path": "db_query.py", "line": 142, "message": "Bypasses the Locked layer."},
    {"tier": 2, "path": "server.py", "line": 30, "message": "Uses retired term 'db_name'."}
  ]
}
```

- `tier`: integer, `1` (blocking) or `2` (minor).
- `path`: file path relative to repo root.
- `line`: the line number of the issue (use the line in the PR's version of
  the file; `0` if not line-specific).
- `message`: one sentence explaining the issue and what to do.

If there are no findings: `{"summary": "...", "findings": []}`.

## Verdict derivation (for reference — the pipeline does this, not you)

- Any Tier-1 finding → label `needs-work`
- Else any Tier-2 finding → label `good-attempt`
- Else → label `agent:approved`
