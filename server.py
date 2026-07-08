"""SQL Buddy MCP Server - Read-only database access via MCP protocol."""

import json
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource

# Ensure db_conn_locked is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_query import (
    CONFIG_FILE,
    check_connection_query,
    execute_limit_query,
    explain_query,
    find_table_query,
    get_history_queries,
    init_dirs,
    list_connections,
    list_tables,
    table_info_query,
    table_schema_query,
)

DEFAULT_CONNECTION = os.environ.get("SQL_BUDDY_DEFAULT_CONNECTION", "ck")

# Parse project-specific table resources: {"ck": ["orders", "users"], "mysql": ["products"]}
_RESOURCES_CONFIG: dict = {}
_raw = os.environ.get("SQL_BUDDY_RESOURCES")
if _raw:
    try:
        _RESOURCES_CONFIG = json.loads(_raw)
    except json.JSONDecodeError:
        pass

init_dirs()

# One-time setup nudge: never auto-run the wizard, just tell the user to.
# init.py is invoked manually (`python init.py`) so normal use never prompts.
if not os.path.exists(CONFIG_FILE):
    sys.stderr.write(
        f"[sql-buddy] No db.ini found at {CONFIG_FILE}\n"
        "[sql-buddy] Configure once with:  python init.py\n"
    )


# --- agent-friendly error hints -------------------------------------------
# Tools return JSON. On failure we add `connection` (which endpoint failed) and
# `hint` (the next action an agent should take) so agents can react programmably.

def _hint_for(exc, connection: str | None = None) -> dict:
    """Map an exception to an actionable hint dict (possibly empty)."""
    msg = str(exc)
    low = msg.lower()
    cls = exc.__class__.__name__
    if isinstance(exc, AssertionError):
        return {"hint": "Only SELECT/SHOW/DESCRIBE/EXPLAIN/USE/WITH/EXISTS are allowed (read-only)."}
    if isinstance(exc, PermissionError):
        return {"hint": "Write operations are blocked by design - sql-buddy is read-only."}
    if 'no section' in low or 'unknown connection' in low or cls == 'AttributeError':
        return {"hint": f"Unknown connection '{connection}'. Call list_connections to list valid names."}
    if any(k in low for k in ('refused', 'timed out', 'timeout', 'connect', 'socket',
                              ' eof', 'closed', 'tunnel')):
        return {"hint": "Transport/connection error. Call check_connection to confirm; in External "
                        "tunnel mode the user's SSH tunnel may be down. Call list_connections to verify config."}
    if any(k in low for k in ('access denied', 'authentication', 'auth', 'password', 'login')):
        return {"hint": "DB authentication failed - ask the user to check user/password in db.ini."}
    if 'table' in low and any(k in low for k in ('exist', 'not found', 'unknown')):
        return {"hint": "Table not found on this connection. Call find_table to locate it across all connections."}
    return {}


def _error_json(exc, connection: str | None = None) -> str:
    payload = {"error": str(exc)}
    if connection:
        payload["connection"] = connection
    payload.update(_hint_for(exc, connection))
    return json.dumps(payload, default=str)


mcp = FastMCP("sql-buddy", instructions=(
    "Read-only database query server. Agents MUST NOT write to the database - use this MCP for reads only. "
    "Tools: `query` (SELECT/SHOW/DESCRIBE/EXPLAIN/WITH), `get_table_definition` (CREATE TABLE + parsed columns), "
    "`get_info` (row count + indexes/partitions), `list_tables` (ALWAYS pass `like` e.g. '%market%' - never fetch all then grep), "
    "`find_table` (locate a table across ALL connections by name closeness), `list_connections` (discover configured endpoints), "
    "`get_history` (past queries), `explain` (EXPLAIN plan + cost), `check_connection` (ping one connection). "
    "`connection` names a db.ini section (e.g. 'ck'); `database` in list_tables names a table namespace inside that server. "
    "LIMIT is auto-added for SELECT/WITH (default 10, max 2000) - write an explicit `LIMIT N` (up to 2000) for more rows. "
    "Error recovery playbook: on a connection/transport error, call `check_connection` then `list_connections` to verify the name; "
    "if a table is missing on a guessed connection, call `find_table` and re-query the top match's `connection`+`qualified_name`; "
    "a 'write blocked' error is expected (read-only). Every error JSON includes a `hint` field with the suggested next action - follow it. "
    "Tunnels: sql-buddy either opens its own SSH tunnel (Auto mode) or expects one the user runs externally (External mode). "
    "Connection failures in External mode usually mean the user's tunnel is down - ask them to check it."
))


# --- Resources (from SQL_BUDDY_RESOURCES env var) ---

if _RESOURCES_CONFIG:
    @mcp.resource("resource://tables", name="configured-tables",
                   description="List of all configured table resources")
    def list_configured_tables() -> str:
        return json.dumps(_RESOURCES_CONFIG, indent=2)

    for _connection, _table_list in _RESOURCES_CONFIG.items():
        if not isinstance(_table_list, list):
            continue
        for _tbl in _table_list:
            _uri = f"resource://{_connection}/{_tbl}/schema"
            mcp.add_resource(FunctionResource.from_function(
                fn=lambda _c=_connection, _t=_tbl: json.dumps(
                    table_schema_query(_t, _c), indent=2, default=str
                ),
                uri=_uri,
                name=f"{_connection}.{_tbl}",
                description=f"Schema for {_connection}.{_tbl} (CREATE TABLE + parsed columns)",
                mime_type="application/json",
            ))


@mcp.tool()
def query(sql: str, connection: str = DEFAULT_CONNECTION) -> str:
    """Execute a read-only SQL query. Returns JSON with columns and rows.
    LIMIT is auto-added if missing (default 10, max 2000); write an explicit
    `LIMIT N` (up to 2000) in the SQL to get more rows. On error the JSON
    includes `connection` and a `hint` with the suggested next action."""
    try:
        return execute_limit_query(sql, connection)
    except Exception as e:
        return _error_json(e, connection)


@mcp.tool()
def check_connection(connection: str = DEFAULT_CONNECTION) -> str:
    """Verify a connection is reachable (lightweight SELECT 1 round-trip).
    Returns JSON {connection, status:'ok', latency_ms} on success, or
    {connection, status:'error', error, hint} on failure. Use this to diagnose
    tunnel/connectivity problems before running real queries, or to confirm a
    setup change. In External tunnel mode, an error usually means the user's
    SSH tunnel is down."""
    try:
        return check_connection_query(connection)
    except Exception as e:
        payload = {"connection": connection, "status": "error", "error": str(e)}
        payload.update(_hint_for(e, connection))
        return json.dumps(payload, default=str)


@mcp.tool()
def get_table_definition(table_name: str, connection: str = DEFAULT_CONNECTION, force_refresh: bool = False) -> str:
    """Get CREATE TABLE statement for a table. Returns JSON with schema, parsed columns, engine, partition, order_by. Cached for 3 days."""
    try:
        result = table_schema_query(table_name, connection, force_refresh)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return _error_json(e, connection)


@mcp.tool()
def get_info(table_name: str, connection: str = DEFAULT_CONNECTION, force_refresh: bool = False) -> str:
    """Get table metadata: row count, indexes, partitions. Cached for 3 days."""
    try:
        result = table_info_query(table_name, connection, force_refresh)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return _error_json(e, connection)


@mcp.tool()
def get_history(connection: str = DEFAULT_CONNECTION, table_name: str | None = None) -> str:
    """Get query execution history, optionally filtered by table."""
    try:
        result = get_history_queries(connection, table_name)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return _error_json(e, connection)


@mcp.tool()
def list_connections_tool() -> str:
    """List available connection options from config. Use this to discover which connections you can query."""
    try:
        return list_connections()
    except Exception as e:
        return _error_json(e)


@mcp.tool()
def list_tables_tool(connection: str = DEFAULT_CONNECTION, database: str | None = None, like: str | None = None) -> str:
    """List tables in a connection.

    `connection` names the DB endpoint (a db.ini section). `database` optionally
    names a table namespace inside it. ALWAYS use `like` param to search for
    specific tables - e.g. like='%order%' finds tables containing 'order' in name.
    This is server-side SQL LIKE, much faster than fetching all tables then grepping.
    Only omit `like` when you genuinely need full listing.
    """
    try:
        return list_tables(connection, database, like)
    except Exception as e:
        return _error_json(e, connection)


@mcp.tool()
def find_table(search: str, limit: int = 10) -> str:
    """Find the table closest to `search` across ALL connections.

    Searches every configured connection (e.g. ck, ck-tk, ck-amazon, db-95-rw),
    ranks candidates by name closeness (exact > substring > token > fuzzy), and
    returns each match with `connection`, `database`, `table`, `engine`, `score`,
    `match_type`, and a `qualified_name` (`database.table`) you can drop into SQL.

    Call this when: you are unsure which connection/database holds a table, OR a
    query / `get_table_definition` / `get_info` failed because you guessed the
    wrong connection. Then re-run the real query against the top match's
    `connection` and `qualified_name`.
    """
    try:
        return find_table_query(search, limit)
    except Exception as e:
        return _error_json(e)


@mcp.tool()
def explain(sql: str, connection: str = DEFAULT_CONNECTION) -> str:
    """Run EXPLAIN on a query and return execution plan with cost estimates. Use to analyze query performance."""
    try:
        return explain_query(sql, connection)
    except Exception as e:
        return _error_json(e, connection)


if __name__ == "__main__":
    mcp.run(transport="stdio")
