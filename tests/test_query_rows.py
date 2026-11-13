"""Pins for the pandas-free query path (2026-11-13 subtraction).

Receipt: pandas was a 45 MiB-per-process import used purely as a row
formatter — and it sat on EVERY hot path (check_connection, every query,
list_tables, get_info, EXPLAIN), so it loaded within seconds of any use.
These tests pin the removal: importing the whole server must leave pandas
unloaded, the dialect dispatcher must produce the exact same JSON shape for
both dialects, and the new `query_rows` read path must route through the
Locked layer's read-only filter (Tier 1 invariant).
"""

import json
import subprocess
import sys
import tomllib
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connections.wrappers import (  # noqa: E402
    LockedMySQLConnectionWrapper,
    MySQLConnectionWrapper,
)


# --- the pin: pandas never loads ---------------------------------------------

def test_importing_server_does_not_load_pandas():
    """The whole app import graph must be pandas-free (fresh interpreter)."""
    code = (
        "import sys;"
        "import server;"                       # pulls db_query + connections + mcp
        "assert 'pandas' not in sys.modules, sorted(m for m in sys.modules if 'pandas' in m);"
        "print('pandas-free')"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60, cwd=str(Path(__file__).resolve().parent.parent))
    assert out.returncode == 0, out.stderr
    assert "pandas-free" in out.stdout


def test_pandas_not_a_declared_dependency():
    """pandas stays out of pyproject dependencies (installs get lighter too)."""
    data = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml")
                         .read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert not any("pandas" in d for d in deps), f"pandas crept back: {deps}"


# --- wrappers: query_rows semantics + Locked filtering ------------------------

class _DictCursor:
    description = [("Tables_in_x", 253), ("Create Table", 252)]

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql):
        self.sql = sql

    def fetchall(self):
        return self._rows

    def close(self):
        pass


def _mysql_conn(rows):
    conn = MagicMock()
    conn.cursor.return_value = _DictCursor(rows)
    return conn


def test_query_rows_returns_columns_and_dict_rows():
    rows = [{"Tables_in_x": "t1", "Create Table": "CREATE TABLE t1 (...)"},
            {"Tables_in_x": "t2", "Create Table": "CREATE TABLE t2 (...)"}]
    w = MySQLConnectionWrapper(_mysql_conn(rows))
    columns, out = w.query_rows("SHOW TABLES")
    assert columns == ["Tables_in_x", "Create Table"]
    assert out == rows


def test_locked_query_rows_filters_write_sql():
    """Tier 1: the new read path must route through the read-only guard."""
    import connections.wrappers as wrappers_mod
    w = LockedMySQLConnectionWrapper(MySQLConnectionWrapper(_mysql_conn([])))
    with pytest.raises(Exception, match="(?i)read.?only|write|not allowed|forbidden|security"):
        w.query_rows("DELETE FROM t1")
    # and a plain read passes through to the wrapped client
    columns, out = w.query_rows("SELECT 1")
    assert columns == ["Tables_in_x", "Create Table"]


# --- db_query dispatcher: same JSON shape for both dialects -------------------

class _FakeMySQL:
    """Answers query_rows (MySQL wrapper interface)."""

    def query_rows(self, sql):
        return ["id", "amount"], [{"id": 1, "amount": Decimal("1.50")},
                                  {"id": 2, "amount": 2}]


class _FakeClickHouse:
    """Answers execute(with_column_types=True) (Locked CH interface)."""

    def execute(self, sql, with_column_types=False, **kw):
        assert with_column_types is True
        return [(1, Decimal("1.50")), (2, 2)], [("id", "UInt32"), ("amount", "Decimal")]


def test_result_to_json_identical_shape_both_dialects():
    from db_query import _result_to_json
    mysql = json.loads(_result_to_json(_FakeMySQL(), "SELECT ..."))
    ch = json.loads(_result_to_json(_FakeClickHouse(), "SELECT ..."))
    expected = {
        "columns": ["id", "amount"],
        "rows": [{"id": 1, "amount": 1.5}, {"id": 2, "amount": 2}],
        "row_count": 2,
    }
    assert mysql == expected
    assert ch == expected          # dialect-identical, decimals floated


class _EmptyClickHouse:
    def execute(self, sql, with_column_types=False, **kw):
        return [], []


def test_result_to_json_empty_result():
    from db_query import _result_to_json, _fetch_rows
    assert json.loads(_result_to_json(_EmptyClickHouse(), "SELECT ...")) == {
        "columns": [], "rows": [], "row_count": 0}
    assert _fetch_rows(_EmptyClickHouse(), "SELECT ...") == ([], [])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
