"""Single source of truth for the sql-buddy version.

Keep this literal in sync with `version` in pyproject.toml. We keep a literal
(rather than reading only from installed metadata) so that vendored/offline
distributions and bare `python server.py` runs without an installed dist can
still report a version. `cli.get_version()` prefers installed metadata when
available and falls back to this.
"""

__version__ = "0.3.0"
