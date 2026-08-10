# tests/

Golden + unit tests for sql-buddy. Two kinds:

| File                       | Needs a live DB? | Notes                                                              |
| -------------------------- | ---------------- | ------------------------------------------------------------------ |
| `test_parse_create_table.py` | No             | DDL parsing: PRIMARY KEY vs ORDER BY, SAMPLE BY, time-column extraction. |
| `test_sanitize_search.py`   | No             | `_sanitize_search` strips non-word chars, keeps `_`/digits.        |
| `test_get_info.py`          | Yes (skippable)| Golden output of `get_info` on real `ck` / `db`; auto-skipped when the connection is absent from db.ini. |

## Running

With pytest (collects all three; live-DB tests skip without a configured
connection):

```bash
pytest tests/
```

Without pytest (each file has a standalone runner):

```bash
python3 tests/test_parse_create_table.py
python3 tests/test_sanitize_search.py
python3 tests/test_get_info.py        # live-DB tests SKIP without db.ini
```

## Live-DB tests

The `test_get_info_*` tests pin the exact output the maintainer sees on the real
endpoints (e.g. `asia.elevenst_kr_seo_result` has 71 rows). They are **skipped**
unless the matching connection (`ck` for ClickHouse, `db` for MySQL) is a
section in `db.ini`. Configure those sections (see `init.py`) and they run for
real; otherwise they never hard-fail in CI / sandboxes without secrets.
