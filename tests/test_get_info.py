"""Golden tests for get_info (the `get_info` MCP tool -> table_info_query).

Two tiers of test live here:

  * **Live-DB (golden)** — these pin the exact output the maintainer sees on the
    real ClickHouse / MySQL endpoints. They are SKIPPED automatically when the
    relevant connection is not present in db.ini (e.g. in CI / sandboxes without
    secrets), so they never hard-fail there. Configure `ck` and `db` and they
    run for real.

  * The ClickHouse test asserts the #26 fix: get_info no longer fabricates a
    MySQL-style PRIMARY index for ClickHouse (CH has no such thing); it reports
    `parts` from `system.parts` instead.

Run with `pytest` or standalone (`python3 tests/test_get_info.py`).
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pytest
except ImportError:  # pragma: no cover - exercised only when pytest is absent
    pytest = None

from db_query import CONFIG_FILE, DBManager, table_info_query  # noqa: E402


def _connection_configured(connection: str) -> bool:
    """True iff `connection` is a section in db.ini (no network probe).

    Fast and safe: never opens a socket, so collecting this module never blocks
    on a down tunnel. The real query inside the test surfaces a transport error
    as a genuine failure when the connection is configured but unreachable.
    """
    if not os.path.exists(CONFIG_FILE):
        return False
    try:
        cfg = DBManager.load_config(CONFIG_FILE)
        return connection.replace('_', '-') in cfg.sections()
    except Exception:
        return False


def _needs(connection: str):
    """A skipif decorator when pytest is present; a passthrough otherwise."""
    if pytest is not None:
        return pytest.mark.skipif(
            not _connection_configured(connection),
            reason=f"requires a configured '{connection}' connection in db.ini",
        )

    def _deco(fn):
        return fn
    return _deco


@_needs("ck")
def test_get_info_clickhouse_returns_parts_not_indexes():
    info = table_info_query("asia.elevenst_kr_seo_result", "ck", force_refresh=True)
    assert "indexes" not in info
    assert "parts" in info or "partitions" in info
    assert info["row_count"] == 71


@_needs("db")
def test_get_info_mysql_returns_real_indexes():
    info = table_info_query("git.review", "db", force_refresh=True)
    assert "indexes" in info
    assert any(i["Key_name"] == "PRIMARY" and i["Column_name"] == "id" for i in info["indexes"])
    assert any(i["Cardinality"] > 0 for i in info["indexes"])


if __name__ == "__main__":
    _fns = [
        (n, f) for n, f in inspect.getmembers(sys.modules[__name__], inspect.isfunction)
        if n.startswith("test_")
    ]
    _passed = _failed = _skipped = 0
    for _name, _fn in sorted(_fns):
        # Map a test to the connection it needs by name convention.
        _conn = "ck" if "clickhouse" in _name else "db"
        if not _connection_configured(_conn):
            print(f"SKIP  {_name} (connection '{_conn}' not configured)")
            _skipped += 1
            continue
        try:
            _fn()
            print(f"PASS  {_name}")
            _passed += 1
        except Exception as _e:  # noqa: BLE001
            print(f"FAIL  {_name} -> {_e}")
            _failed += 1
    print(f"\n{_passed} passed, {_failed} failed, {_skipped} skipped")
    sys.exit(1 if _failed else 0)
