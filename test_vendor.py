"""Sanity-check that all vendored dependencies can be imported.

Run from the repo root (e.g. `python test_vendor.py`). Does NOT import the
application itself (which tries to read db.ini) — just the libraries.
"""

import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor"))
if sys.platform == "win32":
    for _extra in (
        os.path.join("_vendor", "win32", "lib"),
        os.path.join("_vendor", "win32"),
        os.path.join("_vendor", "pythonwin"),
    ):
        _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _extra)
        if os.path.isdir(_path) and _path not in sys.path:
            sys.path.insert(0, _path)
    _dll = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor", "pywin32_system32")
    if os.path.isdir(_dll):
        try:
            os.add_dll_directory(_dll)
        except OSError:
            pass

MODULES = [
    "clickhouse_driver",
    "pymysql",
    "paramiko",
    "mcp",
    "mcp.server.fastmcp",
    "pydantic",
    "httpx",
    "pythoncom",
    "pywintypes",
]

errors = []
for mod in MODULES:
    try:
        __import__(mod)
        print(f"  OK  {mod}")
    except Exception as e:
        errors.append((mod, str(e)))
        print(f"FAIL  {mod}: {e}")

if errors:
    print(f"\n{len(errors)} module(s) failed.")
    sys.exit(1)
print("\nAll vendored imports OK.")
