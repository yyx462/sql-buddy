"""SQL Buddy MCP Server - Read-only database access via MCP protocol."""

import json
import os
import sys

from mcp.server.fastmcp import FastMCP

# Ensure db_conn_locked is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_query import (
    execute_limit_query,
    explain_query,
    get_history_queries,
    init_dirs,
    list_databases,
    list_tables,
    table_info_query,
    table_schema_query,
)

init_dirs()

mcp = FastMCP("sql-buddy", instructions=(
    "Agents SHOULD NOT attempt to do write to database anywhere and SHOULD use this MCP for database read."
    "Read-only database query server. "
    "Use `query` to execute SELECT/SHOW/DESCRIBE/EXPLAIN/WITH. "
    "Use `get_schema` to fetch table CREATE TABLE. "
    "Use `get_info` for row counts and indexes. "
    "Use `get_history` to see past queries. "
    "Use `list_databases` to discover available db_name options. "
    "Use `list_tables` to list tables in a database, with optional LIKE filter. "
    "Use `explain` to run EXPLAIN on a query and get cost estimates. "
    "LIMIT is auto-added (default 10, max 100) for SELECT and WITH queries. "
    "All operations are read-only."
))


@mcp.tool()
def query(sql: str, db_name: str = "ck") -> str:
    """Execute a read-only SQL query. Returns JSON with columns and rows.
    LIMIT is auto-added if missing (default 10, max 100)."""
    try:
        result = execute_limit_query(sql, db_name)
        return result
    except AssertionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_schema(table_name: str, db_name: str = "ck", force_refresh: bool = False) -> str:
    """Get CREATE TABLE statement for a table. Returns JSON with schema, parsed columns, engine, partition, order_by. Cached for 3 days."""
    try:
        result = table_schema_query(table_name, db_name, force_refresh)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_info(table_name: str, db_name: str = "ck", force_refresh: bool = False) -> str:
    """Get table metadata: row count, indexes, partitions. Cached for 3 days."""
    try:
        result = table_info_query(table_name, db_name, force_refresh)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_history(db_name: str = "ck", table_name: str | None = None) -> str:
    """Get query execution history, optionally filtered by table."""
    try:
        result = get_history_queries(db_name, table_name)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_databases_tool() -> str:
    """List available db_name options from config. Use this to discover which databases you can query."""
    try:
        return list_databases()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_tables_tool(db_name: str = "ck", database: str | None = None, like: str | None = None) -> str:
    """List tables in a database. Optionally filter with LIKE pattern (e.g. 'test_%')."""
    try:
        return list_tables(db_name, database, like)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def explain(sql: str, db_name: str = "ck") -> str:
    """Run EXPLAIN on a query and return execution plan with cost estimates. Use to analyze query performance."""
    try:
        return explain_query(sql, db_name)
    except AssertionError as e:
        return json.dumps({"error": str(e)})
    except Exception as e:
        return json.dumps({"error": str(e)})


if __name__ == "__main__":
    mcp.run(transport="stdio")
