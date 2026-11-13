"""Version single-source-of-truth guards.

`pyproject.toml` `version` is what PyPI publishes (release.yml builds from it),
`_version.py` is the offline fallback `cli.get_version()` reads, `uv.lock`
records the project's own version and only changes when someone runs
`uv lock`, and README.md shows the release via a live PyPI badge precisely so
no human ever hardcodes a number there again.

Each of these has drifted from the others during release bumps, and the drift
is silent until something breaks (a stale lock confusing `uv sync`, an old
banner in `sql-buddy doctor`). These tests make any drift fail loudly, so the
only place a version is ever typed is pyproject.toml (+ `_version.py`, kept
literal by design — see its docstring).
"""

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_pyproject_and_version_py_agree():
    m = re.search(r'__version__\s*=\s*"([^"]+)"',
                  (ROOT / "_version.py").read_text(encoding="utf-8"))
    assert m, "_version.py must define a literal __version__"
    assert m.group(1) == _pyproject_version(), (
        "_version.py and pyproject.toml versions disagree; "
        "_version.py is the offline fallback for cli.get_version()"
    )


def test_uv_lock_matches_pyproject():
    """uv.lock pins the project's own version; regenerate with `uv lock`."""
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    m = re.search(r'name = "sqlbuddy-nwi"\nversion = "([^"]+)"', lock)
    assert m, "uv.lock must contain the sqlbuddy-nwi package entry"
    assert m.group(1) == _pyproject_version(), (
        "uv.lock records an older project version — run `uv lock` after "
        "bumping pyproject.toml"
    )


def test_readme_shows_live_badge_not_hardcoded_version():
    """README release number comes from the PyPI badge, never a literal."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "img.shields.io/pypi/v/sqlbuddy-nwi" in readme, (
        "README must link the PyPI version badge (always-latest display)"
    )
    hardcoded = re.findall(r"current release \*\*\d+\.\d+\.\d+\*\*", readme)
    assert not hardcoded, f"README hardcodes a release number again: {hardcoded}"


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))
