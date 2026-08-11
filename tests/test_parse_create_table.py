"""Unit tests for CREATE TABLE parsing (ClickHouse DDL).

Pure-logic: no live database required. Runs under `pytest` or standalone
(`python3 tests/test_parse_create_table.py`).

Covers the #26 fixes:
  * PRIMARY KEY is extracted distinct from ORDER BY (an ORDER BY column that is
    NOT in PRIMARY KEY is a full granule scan — agents must be able to tell).
  * SAMPLE BY (sampling key) is extracted.
  * _extract_time_columns returns clean names (no bare backtick, no
    backtick-wrapped token).
"""

import inspect
import os
import sys

# Flat-layout modules live at the repo root; put it on sys.path so this file
# works whether run via `pytest` or `python3 tests/test_<name>.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_query import _extract_time_columns, _parse_create_table  # noqa: E402


def test_parse_ch_explicit_primary_key_distinct_from_order_by():
    schema = ("CREATE TABLE t (id UInt32, ts DateTime) "
              "ENGINE = MergeTree PRIMARY KEY id ORDER BY (id, ts)")
    parsed = _parse_create_table(schema)
    assert parsed["primary_key"] == "id"
    assert parsed["order_by"] == "(id, ts)"
    assert parsed["sampling_key"] == ""


def test_parse_ch_sampling_key():
    schema = ("CREATE TABLE t (id UInt32, city String) "
              "ENGINE = MergeTree ORDER BY (id, city) SAMPLE BY city")
    parsed = _parse_create_table(schema)
    assert parsed["sampling_key"] == "city"


def test_extract_time_columns_strips_backticks():
    schema = "CREATE TABLE t (`created_at` DateTime) ENGINE = MergeTree ORDER BY created_at"
    cols = _extract_time_columns(schema)
    assert cols == ["created_at"]
    assert "`" not in cols


def test_parse_real_multiline_ch_ddl():
    """Regression guard: real SHOW CREATE TABLE output (newline-separated)."""
    schema = (
        "CREATE TABLE asia.elevenst_kr_seo_result\n"
        "(\n"
        "    `task_id` String,\n"
        "    `crawl_time` DateTime,\n"
        "    `insert_time` DateTime\n"
        ")\n"
        "ENGINE = MergeTree\n"
        "PRIMARY KEY task_id\n"
        "ORDER BY (task_id, crawl_time)\n"
        "SETTINGS index_granularity = 8192\n"
    )
    parsed = _parse_create_table(schema)
    assert parsed["table_name"] == "asia.elevenst_kr_seo_result"
    assert parsed["engine"] == "MergeTree"
    assert parsed["primary_key"] == "task_id"
    assert parsed["order_by"] == "(task_id, crawl_time)"
    assert parsed["partition"] == ""
    assert parsed["sampling_key"] == ""
    assert [c["name"] for c in parsed["columns"]] == ["task_id", "crawl_time", "insert_time"]
    # crawl_time/insert_time are the time columns; no stray backticks.
    assert _extract_time_columns(schema) == ["crawl_time", "insert_time"]


def test_parse_mysql_pk_in_column_list_does_not_pollute_primary_key():
    """MySQL declares PRIMARY KEY inside the column list; it must not be read as
    a MergeTree sparse-index key (there is no ENGINE-tail PRIMARY KEY clause)."""
    schema = (
        "CREATE TABLE `review` (\n"
        "  `id` int NOT NULL AUTO_INCREMENT,\n"
        "  `name` varchar(255) DEFAULT NULL,\n"
        "  PRIMARY KEY (`id`),\n"
        "  KEY `idx_name` (`name`)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4\n"
    )
    parsed = _parse_create_table(schema)
    assert parsed["primary_key"] == ""
    assert parsed["order_by"] == ""
    assert parsed["engine"] == "InnoDB"


if __name__ == "__main__":
    _fns = [
        (n, f) for n, f in inspect.getmembers(sys.modules[__name__], inspect.isfunction)
        if n.startswith("test_")
    ]
    _passed = _failed = 0
    for _name, _fn in sorted(_fns):
        try:
            _fn()
            print(f"PASS  {_name}")
            _passed += 1
        except Exception as _e:  # noqa: BLE001
            print(f"FAIL  {_name} -> {_e}")
            _failed += 1
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
