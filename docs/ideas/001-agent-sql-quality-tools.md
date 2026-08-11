# 001 — Agent SQL quality tools (design note)

> **Issue:** [#24](https://git.sh.nint.com/ying.yuxiang/sql-buddy/issues/24) —
> *Brainstorm: MCP tools that help agents write better ClickHouse/MySQL SQL.*
> **Status:** PROPOSED · **Scope:** docs-only — no code changes in this issue.

This note captures the brainstorm from #24 as a **ranked shortlist** so we can
pick the single tool to build next. Terminology follows
[`UBIQUITOUS_LANGUAGE.md`](../../UBIQUITOUS_LANGUAGE.md): a **Connection** is a
`db.ini` section (the `connection` param); a **DBMS database** is the table
namespace inside it (the `database` param); a **Table** is reached through one
Connection and lives in one DBMS database; the **Locked** layer is the wrapper
that forces every **Query** to be **Read-only**; the **Schema** of a Table is
its `CREATE TABLE`. Retired terms (`db_name`, `list_databases`, calling a
namespace a "schema") are deliberately avoided below.

---

## 1. Problem — how agents fail at SQL today (with sql-buddy)

SQL Buddy already lets an agent run read-only SQL, locate tables, read
schemas, get row/index/partition info, and see plans. In practice that is
enough to *execute* a query but not enough to *write a good one first try*. The
failure modes we keep seeing:

| Failure mode | What happens | Root gap in sql-buddy |
| --- | --- | --- |
| **Wrong join key** (MySQL) | Agent invents a join column (`orders.user_id = users.id`) that looks plausible but is wrong, or misses a real FK. | No FK / relationship info is exposed; `get_info` shows indexes only as raw rows. |
| **Ignored CH keys** | Agent writes `SELECT … WHERE status = 'x'` against a MergeTree whose `ORDER BY (tenant_id, created_at)` it never read — a full scan across all parts. | `get_table_definition` parses `partition` + `order_by`, but **not** the CH `primary_key` / `sampling_key`, and nothing *promotes* the keys into advice. |
| **MySQL index miss** | Agent filters on an unindexed column and gets a full scan. | `explain` returns raw plan lines; nothing says "access_type=ALL, no key". |
| **Dialect mix-up** | `DATE_FORMAT(...)` sent to ClickHouse, or `formatDateTime(...)` sent to MySQL. | No lint step; the Query just errors at the server. |
| **Over-scan / `SELECT *`** | `SELECT *` on a wide MergeTree, or a filter that skips the partition key. | No preflight or warning before the bytes move. |
| **Opaque errors** | "Unknown column" / "Table not found" / type mismatch — agent retries blindly. | `_hint_for` covers connection/transport/auth/table at a coarse grain; no closest-column or cast suggestion. |
| **Known column, unknown Table** | Agent knows the column is `order_id` but not which Connection/DBMS database holds it. | **No column-level search.** `find_table` is table-name only; the agent resorts to `list_tables` + guessing. |
| **Guessing filter values** | Agent writes `WHERE region = ?` with no idea what `region` actually contains. | No safe sample-data peek (gated on PII policy — see Open Questions). |

The common thread: sql-buddy answers *"can I run this?"* but not *"is this the
right Query, on the right keys, in the right dialect?"* Every candidate tool
below closes one of those gaps **without** weakening the read-only invariant.

### What we already have (and where it stops short)

| Existing tool | Gives the agent | Still missing |
| --- | --- | --- |
| `query` | read-only execution, auto LIMIT | no preflight, no coaching |
| `find_table` | table location across all Connections | column-level search |
| `get_table_definition` | `CREATE TABLE`, parsed columns, `partition`, `order_by` | CH `primary_key`/`sampling_key`, MySQL FK join hints, advice |
| `get_info` | row count, indexes (MySQL), partitions (CH) | raw, un-coached |
| `explain` | plan + cost regex | no "why slow" bullets, no scan-size warning |
| `get_history` | past Queries (audit log) | — |
| `_hint_for` (internal) | coarse error → next action | no closest-column / cast suggestions |

---

## 2. Ranked shortlist (top 5)

Effort sizing used below: **S** ≈ a single new function + thin MCP tool,
<1 day; **M** ≈ new introspection logic + cache reuse, 1–3 days; **L** ≈ new
heavy dependency or cross-cutting feature, >3 days. Every tool is ranked on
(agent-impact × feasibility) **subject to** not weakening the Locked layer.

### #1 — `search_columns`

**Purpose.** Find columns by name (and optional type) across **all**
Connections, so an agent that knows `order_id` learns which Tables — and which
Connection / DBMS database — actually hold it. Fills the largest existing gap:
there is no column-level search today.

**Inputs / outputs.**

```jsonc
// in
search_columns(search = "order_id", type = "uint64", limit = 20)

// out
{
  "search": "order_id", "normalized_search": "orderid",
  "matches": [
    { "connection": "ck", "database": "sales", "table": "orders",
      "column": "order_id", "type": "UInt64", "position": 1,
      "qualified_name": "sales.orders", "score": 1.0, "match_type": "exact" }
  ],
  "total_matches": 1,
  "searched_connections": [
    { "name": "ck", "type": "clickhouse", "status": "ok", "match_count": 1 }
  ]
}
```

**CH vs MySQL.**

- ClickHouse: `SELECT database, table, name, type, position FROM system.columns
  WHERE positionCaseInsensitive(name, '<term>') > 0 AND database NOT IN
  ('system','INFORMATION_SCHEMA','information_schema')`.
- MySQL: `SELECT table_schema, table_name, column_name, data_type,
  ordinal_position FROM information_schema.columns WHERE
  LOWER(column_name) LIKE '%<term>%' AND table_schema NOT IN
  ('information_schema','mysql','performance_schema','sys')`.
- Optional `type` filter: CH matches the raw `type` text (e.g. `UInt64`);
  MySQL matches `data_type` (e.g. `bigint`). Normalize loosely.

**Stays read-only.** Issues only `SELECT` against `system.columns` /
`information_schema.columns`, both of which begin with a Read-only keyword and
route through the Locked wrapper (`LockedClickHouseClient.execute` /
`LockedMySQLConnectionWrapper.query_dataframe` both call `_filter_sql` first).
No table data is read — only catalog metadata. A `LIMIT` is applied to the
catalog `SELECT` (default 10, clamped to 2000), reusing `execute_limit_query`
semantics.

**Effort / reuse.** **M.** Mirror `find_table_query` almost verbatim: same
per-Connection fan-out, `_sanitize_search`, `_catalog_query`-style SQL builder,
`_score_table_name`-style ranking, per-Connection error swallowing, and the
`table_list_cache/` freshness pattern. New surface: a `_column_catalog_query`
helper + `search_columns_query` in `db_query.py` and a `@mcp.tool()` in
`server.py`. No new dependencies.

---

### #2 — `get_keys`

**Purpose.** Surface the keys that make or break performance in one focused
"key card": ClickHouse `partition_key` + `sorting_key` + `primary_key` +
`sampling_key`; MySQL indexes **and** foreign-key join hints (suggested `ON`
clauses). This is the #1 CH performance lever — and `get_table_definition`
currently parses only `partition` + `order_by`, never `primary_key` or
`sampling_key`.

**Inputs / outputs.**

```jsonc
// in
get_keys(table_name = "orders", connection = "ck")

// out (ClickHouse)
{
  "connection": "ck", "database": "sales", "table": "orders", "type": "clickhouse",
  "keys": {
    "partition_key": "toYYYYMM(created_at)",
    "sorting_key":   "(tenant_id, created_at)",
    "primary_key":   "(tenant_id, created_at)",
    "sampling_key":  ""
  },
  "advice": [
    "Filter on the leftmost sorting-key column (tenant_id) to prune the sparse index.",
    "Add a partition predicate on created_at (or its toYYYYMM) to prune parts."
  ]
}

// out (MySQL)
{
  "connection": "db-rw", "database": "shop", "table": "orders", "type": "mysql",
  "keys": {
    "indexes": [ { "name": "PRIMARY", "columns": ["id"], "unique": true } ],
    "join_hints": [ { "on": "orders.user_id = users.id", "type": "fk",
                      "from": "shop.orders.user_id", "to": "shop.users.id" } ]
  },
  "advice": [ "Filter on user_id to use the FK index for joins." ]
}
```

**CH vs MySQL.**

- ClickHouse: prefer `SELECT partition_key, sorting_key, primary_key,
  sampling_key FROM system.tables WHERE database = ? AND name = ?` over
  re-parsing `CREATE TABLE` (more reliable than regex; `_parse_create_table`
  already extracts `partition` + `order_by` as a fallback).
- MySQL: indexes via `information_schema.statistics` (column order + uniqueness,
  cleaner than `SHOW INDEX`); FK/join hints via
  `information_schema.KEY_COLUMN_USAGE` joined with `REFERENCED_TABLE_NAME` to
  emit ready-to-use `ON` clauses.

**Stays read-only.** `SELECT` against `system.tables` /
`information_schema.statistics` / `information_schema.KEY_COLUMN_USAGE`, all
through the Locked wrapper. The `advice` strings are derived locally — they are
never executed. No `USE`, no DDL.

**Effort / reuse.** **M.** Composes `table_schema_query` (`db_schema_cache/`)
and `table_info_query` (`db_info_cache/`) for the cached parts, adds two new
small introspection `SELECT`s, and a coaching-rules helper. Add `get_keys_query`
to `db_query.py` + a `@mcp.tool()` in `server.py`. No new dependencies.

---

### #3 — `explain_cost`

**Purpose.** `explain` + plain-language "why slow" coaching + a soft scan-size
estimate. ClickHouse `EXPLAIN indexes = 1 <sql>` shows which primary-key marks
survive; MySQL `EXPLAIN FORMAT=JSON <sql>` exposes `access_type`, `key`, and
`rows`. The tool turns those into bullets ("full scan — no index", "no
partition predicate — all parts read") and, when a scan estimate exceeds a
threshold **without** a partition/key predicate, warns before the bytes move.

**Inputs / outputs.**

```jsonc
// in
explain_cost(sql = "SELECT * FROM sales.orders WHERE status = 'paid'", connection = "ck")

// out
{
  "connection": "ck", "plan": "...", "cost_estimates": { "rows": 120000000 },
  "estimated_rows_read": 120000000,
  "advice": [
    { "severity": "warn", "issue": "no_partition_predicate",
      "message": "Filter does not touch partition key (toYYYYMM(created_at)); all parts read." },
    { "severity": "warn", "issue": "select_star_on_mergetree",
      "message": "SELECT * on a MergeTree reads every column; project only what you need." }
  ]
}
```

**CH vs MySQL.**

- ClickHouse: `EXPLAIN <sql>` for the `Read` row estimate; `EXPLAIN indexes = 1
  <sql>` to see primary-key mark pruning. Advice rules: missing
  partition/sorting-key predicate, `SELECT *` on wide MergeTree, FINAL on a
  ReplacingMergeTree.
- MySQL: `EXPLAIN FORMAT=JSON <sql>` → `access_type == "ALL"` ⇒ full scan,
  `key == null` ⇒ index miss, `rows` large ⇒ over-scan. Advice rules: full
  scan, missing index, filesort/temp table.

**Stays read-only.** `EXPLAIN` is a Read-only keyword (listed in
`readonly.READ_ONLY_KEYWORDS` and accepted by `db_query._is_query_allowed`),
so the plan request itself is guarded by the Locked layer exactly like
`explain` is today. The advice is computed locally from the plan JSON; nothing
else is sent to the server.

**Effort / reuse.** **M.** Built on top of `explain_query` (which already runs
`EXPLAIN` and scrapes cost metrics). New work is the JSON variant selector
(`FORMAT=JSON` / `indexes = 1`) and the coaching-rules module. No new
dependencies.

---

### #4 — `diagnose_error`

**Purpose.** Turn an opaque server error into structured, actionable fix
hints. "Unknown column `orde_id`" → closest real column names (via
`search_columns`); "Table not found" → `find_table` top hits; type mismatch →
a cast suggestion; auth/transport → the existing `_hint_for` playbook. Today
`_hint_for` only covers connection/transport/auth/table at a coarse grain.

**Inputs / outputs.**

```jsonc
// in
diagnose_error(error = "Unknown column 'orde_id' in 'field list'", connection = "ck",
               sql = "SELECT orde_id FROM sales.orders")

// out
{
  "error": "Unknown column 'orde_id' in 'field list'",
  "category": "unknown_column",
  "hints": [
    { "action": "search_columns", "suggestion": "order_id",
      "candidates": [
        { "connection": "ck", "database": "sales", "table": "orders", "column": "order_id" }
      ] }
  ]
}
```

**CH vs MySQL.** The classifier keys off error text shape, not dialect: both
say "Unknown column" / "doesn't exist"; ClickHouse wraps it in
`DB::Exception: ...` (already stripped by `clean_db_error`). Type-mismatch
suggestions differ: CH proposes `CAST(x AS UInt64)`; MySQL proposes
`CAST(x AS UNSIGNED)`. The suggestion engine delegates to `search_columns`
(column typos) and `find_table` (table typos), so dialect handling is
inherited from those tools.

**Stays read-only.** `diagnose_error` issues **no SQL of its own** — it is a
pure dispatcher over error text and over other read-only tools. Any SQL it
causes to run is via `search_columns` / `find_table`, both already Locked.

**Effort / reuse.** **M.** Generalizes `server._hint_for` into a richer
classifier; reuses `search_columns_query` (new), `find_table_query`, and
`difflib` for closest-name suggestions. No new dependencies.

---

### #5 — `validate_sql`

**Purpose.** Lint / parse a **Query** **without executing it** — flag MySQL-only
vs ClickHouse-only constructs, `SELECT *` on a wide MergeTree, and a missing
partition predicate. Two implementations, in order of preference: (a) a
**server `EXPLAIN` dry path** (no new dependency — `EXPLAIN` runs the parser
and returns a plan or a syntax error without materializing rows); (b) optional
`sqlglot` parsing for offline dialect checks (adds a dependency — see Open
Questions).

**Inputs / outputs.**

```jsonc
// in
validate_sql(sql = "SELECT DATE_FORMAT(created_at, '%Y') FROM sales.orders",
             connection = "ck")

// out
{
  "connection": "ck", "dialect": "clickhouse",
  "ok": false,
  "findings": [
    { "severity": "error", "rule": "dialect_mismatch",
      "message": "DATE_FORMAT is MySQL syntax; ClickHouse uses formatDateTime(created_at, '%Y')." }
  ],
  "checked_via": "explain_dry_run"
}
```

**CH vs MySQL.** This *is* the dialect tool. With the EXPLAIN dry path: send
`EXPLAIN <sql>`; a parser error means a syntax/dialect problem (classify the
message). With sqlglot (optional): `sqlglot.parse_one(sql, dialect=...)` and
walk the AST for MySQL-only/CH-only functions, bare `SELECT *`, and (joined
with `get_keys`) a missing partition/sorting-key predicate.

**Stays read-only.** Never executes the Query. The EXPLAIN dry path routes
through the Locked layer (EXPLAIN is a Read-only keyword) and never returns
rows. sqlglot mode runs entirely in-process. There is no path by which the
agent's input SQL reaches a database as a runnable statement — `validate_sql`
only ever sends `EXPLAIN <sql>`.

**Effort / reuse.** **M** for the EXPLAIN-dry path (reuses `explain_query` +
`_is_query_allowed`), **L** if sqlglot is vendored into `_vendor/`. Recommend
shipping the M version first.

---

## 3. Non-goals & rejected ideas

- **Write / DML / DDL tools.** Never. Every new execute path must go through
  the Locked layer; this issue proposes none that executes anything but
  read-only introspection.
- **`sample_rows` (candidate A4).** High value but **deferred** — it reads real
  rows and needs a PII redaction profile first (Open Questions). Building it
  without a redaction config risks echoing sensitive data into MCP responses,
  which is a Tier-1 (secret-safety) violation in `AGENTS.md`.
- **Column redaction profiles / `preview_limits` (F18–F19).** Governance
  plumbing; revisit alongside `sample_rows` once a PII policy exists.
- **`translate_sql` / `format_sql` (B7–B8).** Low ROI — an LLM agent already
  translates dialects and formats SQL well; tool surface is better spent on
  facts the model cannot infer (keys, indexes, columns).
- **`refine_query` (D13).** Generates patched SQL and is riskier than the five
  above; defer until `validate_sql` + `diagnose_error` prove out, then build it
  on top of their output (still read-only, still through Locked).
- **`who_uses_table` (E17).** Useful but `query_history/` is a per-Connection
  audit log and often sparse; defer until history coverage is broader.
- **`diff_result_shape` (D14).** Niche; an agent diffing SELECT lists after
  schema drift is low frequency.
- **`list_engines` / "profile card" (E16).** Mostly composes existing tools;
  fold the useful parts into `get_keys` and `get_info` rather than a new tool.
- **Production system.* stats (system.query_log / system.parts).** Out of scope
  here; gated on permissions (Open Questions) and documented as optional in the
  CH priorities.

---

## 4. Recommended first slice — build `search_columns` next

**Tool:** `search_columns(search, connection?, type?, limit?)`.

**Why this one first:**

1. **It fills the largest gap that no existing tool covers.** Today an agent
   can find a Table by name (`find_table`) and read a Table's Schema
   (`get_table_definition`), but it **cannot** find a Table by column. "I know
   the column is `order_id`" is one of the most common starting points and
   currently degrades to `list_tables` + guessing.
2. **It is the lowest-risk read-only add.** It reads only catalog metadata
   (`system.columns` / `information_schema.columns`) through the Locked wrapper;
   it never touches table data, so there is no PII exposure and no path for a
   Write keyword to slip through.
3. **It reuses the most battle-tested pattern in the repo.** `find_table_query`
   already solves "fan out across all Connections, rank candidates, swallow
   per-Connection errors, cache the full list for fuzzy fallback." `search_columns`
   is the same shape with a different catalog table — high confidence it lands
   cleanly.
4. **It unblocks the other four.** `diagnose_error` (closest-column
   suggestions) and `validate_sql` (partition-predicate checks via column
   location) both want a column search underneath; building it first makes
   those cheaper.

**Close second:** `get_keys` — it is the single biggest CH performance lever
and composes data we already cache. Defer it only because `get_keys` partly
overlaps `get_table_definition` (which already exposes `partition` + `order_by`),
whereas `search_columns` has zero overlap with anything shipped today.

---

## 5. Open questions (for a human)

1. **`system.*` / `information_schema.*` permissions.** Do the read-only
   DB users in `db.ini` actually have `SELECT` on `system.columns`,
   `system.tables` (ClickHouse) and `information_schema.columns`,
   `information_schema.statistics`, `information_schema.KEY_COLUMN_USAGE`
   (MySQL)? `search_columns`, `get_keys`, and `diagnose_error` all depend on
   this. If not, we need either a grant or a graceful "permission denied"
   path + fallback.
2. **PII policy.** Which columns are sensitive, and is in-response masking
   acceptable? This gates `sample_rows` and any column-redaction profile.
   Without a decision here we keep `sample_rows` deferred.
3. **`system.query_log` / `system.parts` access.** Allowed for the read-only
   user? If yes, `explain_cost` can use real parts stats; if no, fall back to
   EXPLAIN-only estimates.
4. **Caching model for new introspection.** Do column results get a new
   `db_column_cache/` directory, or reuse `db_schema_cache/`? A new dir keeps
   the 3-day freshness model uniform but widens the on-disk surface.
5. **sqlglot dependency.** Allowed in the vendored `_vendor/` bundle (it is
   pure-Python), or should `validate_sql` stay EXPLAIN-dry-only? The answer
   sets whether `validate_sql` is **M** or **L**.
6. **Default LIMIT for metadata tools.** Keep the global default (10) for
   consistency, or raise it for catalog tools (e.g. 100) so `search_columns`
   returns enough hits to be useful out of the box? Either way the 2000 max
   still applies.
