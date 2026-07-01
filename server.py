"""SQL Buddy MCP Server - Read-only database access via MCP protocol."""

import json
import os
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource

# Ensure db_conn_locked is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_query import (
    execute_limit_query,
    explain_query,
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

mcp = FastMCP("sql-buddy", instructions=(
    "Agents SHOULD NOT attempt to do write to database anywhere and SHOULD use this MCP for database read."
    "Read-only database query server. "
    "Use `query` to execute SELECT/SHOW/DESCRIBE/EXPLAIN/WITH. "
    "Use `get_table_definition` to fetch a table's CREATE TABLE. "
    "Use `get_info` for row counts and indexes. "
    "Use `get_history` to see past queries. "
    "Use `list_connections` to discover available connection options. "
    "Use `list_tables` to list tables in a connection. ALWAYS pass `like` param (SQL LIKE, e.g. '%market%') when searching for specific tables — never fetch all then grep. "
    "Use `explain` to run EXPLAIN on a query and get cost estimates. "
    "Resources expose table schemas for configured tables. Use list_resources to discover them. "
    "LIMIT is auto-added (default 10, max 2000) for SELECT and WITH queries — write an explicit `LIMIT N` (up to 2000) in the SQL to get more rows. "
    "All operations are read-only. "
    "`connection` names a configured DB endpoint (a db.ini section, e.g. 'ck'); `database` in list_tables names a table namespace inside that server."
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
    `LIMIT N` (up to 2000) in the SQL to get more rows."""
    try:
        result = execute_limit_query(sql, connection)
        return result
    except AssertionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_table_definition(table_name: str, connection: str = DEFAULT_CONNECTION, force_refresh: bool = False) -> str:
    """Get CREATE TABLE statement for a table. Returns JSON with schema, parsed columns, engine, partition, order_by. Cached for 3 days."""
    try:
        result = table_schema_query(table_name, connection, force_refresh)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_info(table_name: str, connection: str = DEFAULT_CONNECTION, force_refresh: bool = False) -> str:
    """Get table metadata: row count, indexes, partitions. Cached for 3 days."""
    try:
        result = table_info_query(table_name, connection, force_refresh)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_history(connection: str = DEFAULT_CONNECTION, table_name: str | None = None) -> str:
    """Get query execution history, optionally filtered by table."""
    try:
        result = get_history_queries(connection, table_name)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_connections_tool() -> str:
    """List available connection options from config. Use this to discover which connections you can query."""
    try:
        return list_connections()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_tables_tool(connection: str = DEFAULT_CONNECTION, database: str | None = None, like: str | None = None) -> str:
    """List tables in a connection.

    `connection` names the DB endpoint (a db.ini section). `database` optionally
    names a table namespace inside it. ALWAYS use `like` param to search for
    specific tables — e.g. like='%order%' finds tables containing 'order' in name.
    This is server-side SQL LIKE, much faster than fetching all tables then grepping.
    Only omit `like` when you genuinely need full listing.
    """
    try:
        return list_tables(connection, database, like)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def explain(sql: str, connection: str = DEFAULT_CONNECTION) -> str:
    """Run EXPLAIN on a query and return execution plan with cost estimates. Use to analyze query performance."""
    try:
        return explain_query(sql, connection)
    except AssertionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    mcp.run(transport="stdio")
