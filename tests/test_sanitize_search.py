"""Unit tests for find_table's search-term sanitizer.

Pure-logic: no live database required. Runs under `pytest` or standalone
(`python3 tests/test_sanitize_search.py`).

_sanitize_search strips every non-word character before the term is embedded in
catalog SQL, so quotes/semicolons/dashes cannot break out of the literal. Word
characters (incl. underscore) are preserved so multi-token names like
`order_id` survive intact.
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_query import _sanitize_search  # noqa: E402


def test_sanitize_search_strips_quotes():
    assert _sanitize_search("'; DROP TABLE--") == "DROPTABLE"
    assert _sanitize_search("order_id") == "order_id"


def test_sanitize_search_handles_none_and_empty():
    assert _sanitize_search("") == ""
    assert _sanitize_search(None) == ""


def test_sanitize_search_preserves_underscores_and_digits():
    assert _sanitize_search("orders_2024") == "orders_2024"
    assert _sanitize_search("user.id") == "userid"


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
