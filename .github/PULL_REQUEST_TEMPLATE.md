<!--
  Self-check before requesting review. The rubric lives in AGENTS.md — the two
  invariants below are the blocking ones; they MUST hold. Never paste real
  credentials, SSH keys, hostnames, or IPs in this PR (see CONTRIBUTING.md).
-->

## Summary

<!-- What does this change do, and why? One short paragraph. -->

## Type

- [ ] Bug fix
- [ ] New / changed tool (a `@mcp.tool` endpoint or the `*_query` behind one)
- [ ] Docs / README
- [ ] Refactor / chore

## Self-check against the rubric (AGENTS.md)

**Tier 1 — Blocking (must hold):**

- [ ] **Read-only invariant.** Every SQL path routes through the Locked layer.
      No write keyword (`INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`TRUNCATE`/
      `CREATE`/`GRANT`/…) can reach a real database. No dynamic SQL that could
      evade keyword detection.
- [ ] **Secret / credential safety.** No DB credentials, SSH keys, or `db.ini`
      contents are logged, echoed to stdout, returned in MCP responses, or
      present in this diff.
- [ ] **Correctness.** Error handling is sound; no resource leaks (SSH tunnels,
      DB connections, file handles); no crashes on valid input.

**Tier 2 — Minor:**

- [ ] **Tests.** Tool changes ship tests under `tests/`; pure-logic changes
      (parsing, sanitization, dispatch) have DB-free unit tests.
- [ ] **Domain language.** Used `connection` / `database` correctly; did not
      reintroduce retired terms (`db_name`, calling a namespace a "schema").
      See `UBIQUITOUS_LANGUAGE.md`.
- [ ] **API consistency.** Respected tool/param names and LIMIT semantics
      (auto-added default 10, max 2000).

**Infra hygiene:**

- [ ] Examples, docs, and comments use **placeholders only** — no real IPs,
      bastion ports, key filenames, or internal hostnames.

## How to test

<!-- Commands or steps the reviewer can run. Note if a configured DB connection
is needed (those assertions auto-skip when absent). -->
