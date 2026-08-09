# Reference: tools, configuration, troubleshooting, security

The [root `README.md`](../README.md) covers install → configure → run. This page
is the long-form reference: the MCP tools, environment configuration, a
troubleshooting matrix, and the full security model. For step-by-step setup see
[`setup.md`](setup.md); for Windows/Trae see [`windows-trae.md`](windows-trae.md).

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `query(sql, connection)` | Execute read-only SQL. `LIMIT` auto-added if missing (default 10, max 2000); pass an explicit `LIMIT N` for more rows. Returns JSON. |
| `find_table(search, limit?)` | **Locate a table across ALL connections by name closeness** when you're unsure which connection holds it, or after a wrong-connection error. Returns `connection` + `database` + `qualified_name` to query next. |
| `get_table_definition(table_name, connection)` | Get `CREATE TABLE` with parsed columns, engine, partition. Cached 3 days. |
| `get_info(table_name, connection)` | Get row count, indexes (MySQL) / partitions (ClickHouse). Cached 3 days. |
| `list_tables(connection, database?, like?)` | List tables; always pass `like` to search server-side (fast). |
| `list_connections()` | Discover available connections from `db.ini`. |
| `check_connection(connection)` | **Ping one connection** (`SELECT 1` round-trip). Returns `{status:'ok', latency_ms}` or `{status:'error', hint}`. Use to diagnose a dead tunnel/conn before running real queries. |
| `get_history(connection, table_name?)` | Past query execution history, optionally filtered by table. |
| `explain(sql, connection)` | Run `EXPLAIN`; returns plan + cost estimates. |

All tools are **read-only**. `connection` names a `db.ini` section; `database`
(in `list_tables`) names a table namespace *inside* that server.

**Errors are agent-actionable** — every error JSON includes the `connection`
that failed (when relevant) and a `hint` with the suggested next action: unknown
connection -> `list_connections`; transport error -> `check_connection` (in
External tunnel mode, the user's SSH tunnel may be down); missing table ->
`find_table`. Read and follow the `hint`.

---

## Configuration reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `SQL_BUDDY_DEFAULT_CONNECTION` | `ck` | Connection used when a tool's `connection` arg is omitted. |
| `SQL_BUDDY_RESOURCES` | *(unset)* | Optional JSON `{connection: [table, ...]}` exposing selected table schemas as MCP resources. Advanced. |
| `DB_QUERY_HISTORY_DIR` | `./database` | Where query history + caches are stored. |

```
.mcp.json                             # project-scope MCP client config (uv run server.py)
connections/config/db.ini          # your credentials + ssh blocks (gitignored, never commit)
connections/config/db.ini.example  # template (committed)
database/                             # caches + query history (gitignored)
server.py  cli.py  db_query.py  db_security.py  tunnel.py  init.py  _version.py
```

---

## Troubleshooting

**"SSH tunnel did not come up" / "ssh stderr: …"** -> wrong `key` path (or
unreadable key), wrong bastion `host`/`port`/`user`, the bastion host key
changed, or `local_port` already in use. Test the SSH leg manually:
`ssh -N -p <port> -i <key> <user>@<host> -L 127.0.0.1:<local_port>:<remote_host>:<remote_port>`.

**"Connection refused" / "timed out"** -> the tunnel came up but the DB target
is wrong. `remote_host`/`remote_port` must be the DB's address **as seen from
the bastion**, not your laptop.

**"Unknown connection" / wrong connection used** -> the `connection` name must
match a `[section]` in `db.ini`. Run `list_connections()`. Check
`SQL_BUDDY_DEFAULT_CONNECTION`.

**"Write operation blocked by sql-buddy's read-only guard"** -> working as
intended. SQL Buddy cannot change data; use a different tool.

**Agent keeps querying the wrong database for a table** -> have it call
`find_table("table_name")` first.

**An `ssh -N` process lingers after SQL Buddy exited** -> a hard kill
(`SIGKILL` / power loss) can orphan it; harmless but holds the ports. Reclaim
with `pkill -af 'ssh -N'`.

**`db.ini` shows up in `git status`** -> stop, do **not** commit it. If already
tracked, remove it from history (it holds live credentials):
`git rm --cached connections/config/db.ini`.

---

## Security model

- **Read-only enforced in code**, before any SQL reaches the network. Write
  keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, …) are
  rejected by a guard layer wrapping every connection.
- **Credentials stay local** — in `db.ini` on your machine, never sent to any
  third party.
- **SSH tunnels bind to localhost only** (`127.0.0.1`) — forwarded DB ports
  aren't exposed on your LAN. Key-based auth; the key never leaves your machine.
- **`LIMIT` is always applied** to `SELECT`/`WITH` (default 10, hard cap 2000).
- **`db.ini` is gitignored.** Verify with `git status` before every push.
