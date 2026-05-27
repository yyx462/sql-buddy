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
| `query(sql, db_name)` | Execute read-only SQL. LIMIT auto-added (default 10, max 100). Returns JSON. |
| `get_schema(table_name, db_name)` | Get CREATE TABLE with parsed columns, engine, partition. Cached 3 days. |
| `get_info(table_name, db_name)` | Get row count, indexes, partitions. Cached 3 days. |
| `get_history(db_name, table_name)` | Query execution history, optionally filtered by table. |

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
