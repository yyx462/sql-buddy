# AGENTS.md — SQL Buddy

This file defines the coding standards and the review rubric in one place so
they never drift. The full domain language (Connection, Query, Locked layer,
etc.) lives in `UBIQUITOUS_LANGUAGE.md` — read it before touching anything
security-sensitive.

## What SQL Buddy is

A read-only MCP server that lets an AI agent query MySQL and ClickHouse
databases. Every path is forced read-only via a **Locked** layer that blocks
write keywords before they reach the network. The crown jewel is this
read-only invariant — protect it above all else.

## Review rubric

When reviewing a pull request, classify each finding into one of two tiers.

### Tier 1 — Blocking

1. **Read-only invariant.** Any code path that could send a Write keyword
   (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`,
   `GRANT`, …) to a real database, or that bypasses, weakens, or circumvents
   the **Locked** layer. Every new tool or endpoint that issues SQL MUST route
   it through the locked executor. Dynamic SQL that could evade keyword
   detection is a Tier-1 finding.

2. **Secret / credential safety.** Database credentials, SSH tunnel private
   keys, `db.ini` connection configs, or API tokens must never be logged,
   echoed to stdout, committed to the repo, or returned in MCP responses or
   error messages.

3. **Correctness.** Logic errors, broken error handling, crashes on valid
   input, resource leaks (unclosed SSH tunnels, DB connections, or file
   handles), race conditions.

4. **Tests for tool changes.** Every PR that adds or changes a tool (a
   `@mcp.tool` endpoint, or the `*_query` function behind one) ships its own
   tests under `tests/`. Pure-logic changes (parsing, sanitization, dispatch)
   MUST include unit tests that run without a live database; assertions that
   need a configured DB connection are skipped when that connection is absent.
   A tool PR with no tests is incomplete.

### Tier 2 — Minor

5. **Ubiquitous-language discipline.** Reintroducing retired terms
   (`db_name`, `list_databases`, calling a namespace a "schema") or conflating
   **Connection** (a `db.ini` section / the `connection` param) with
   **DBMS database** (a table namespace / the `database` param). See
   `UBIQUITOUS_LANGUAGE.md` — the "Resolved ambiguities" section is the
   authoritative list.

6. **API consistency.** Respecting tool and parameter names, LIMIT semantics
   (auto-added default 10, max 2000), and the cache model (`query_history/`
   audit log vs `db_info_cache/` + `db_schema_cache/` with 3-day freshness).

7. **Quality.** Naming, style, small refactors, doc accuracy, obvious
   performance regressions (e.g. unbounded result sets, N+1 introspection
   queries).

### Below the bar

Trivial nitpicks (a missing trailing newline, subjective preference) are
**ignored, not commented**. Keep comments signal-heavy.
