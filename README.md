# SQL Buddy

MCP server for read-only database access (MySQL, ClickHouse).

## Setup

```bash
uv venv .venv --python 3.11
uv pip install -e .
```

Copy `db_conn_locked/config/db.ini.example` to `db_conn_locked/config/db.ini` and fill in credentials.

## Run

```bash
.venv/bin/python server.py
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `query(sql, connection)` | Execute read-only SQL. LIMIT auto-added (default 10, max 100). Returns JSON. |
| `get_table_definition(table_name, connection)` | Get CREATE TABLE with parsed columns, engine, partition. Cached 3 days. |
| `get_info(table_name, connection)` | Get row count, indexes, partitions. Cached 3 days. |
| `get_history(connection, table_name)` | Query execution history, optionally filtered by table. |
| `list_connections()` | Discover available connection options from `db.ini`. |
| `list_tables(connection, database?, like?)` | List tables; always pass `like` to search server-side. |
| `explain(sql, connection)` | Run `EXPLAIN`; returns plan + cost estimates. |

Set the default connection with `SQL_BUDDY_DEFAULT_CONNECTION` (defaults to `ck`). Each `db.ini` section declares `type = clickhouse|mysql` explicitly; the section-name prefix is used as a fallback.

## Claude Code Config

Add to `.claude/settings.json` or your global settings:

```json
{
  "mcpServers": {
    "sql-buddy": {
      "command": "/home/nwi/sql-buddy/.venv/bin/python",
      "args": ["/home/nwi/sql-buddy/server.py"]
    }
  }
}
```
