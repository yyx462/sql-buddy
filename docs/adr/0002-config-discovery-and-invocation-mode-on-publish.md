# Published package resolves config and advertises its invocation by auto-detecting checkout vs install

Once published to PyPI, `uvx --from sqlbuddy-nwi sql-buddy` runs from uv's
ephemeral package cache (the dist is `sqlbuddy-nwi`; see ADR-0004 for why the
name differs from the command), so the existing `__file__`-relative `db.ini` resolution (duplicated in
`init.py`, `cli.py`, `tunnel.py`) reads and writes inside a read-only cache
that uv can evict at any time. sql-buddy instead resolves config via a 3-place
search order, and `mcp add` / `init` / `doctor` auto-detect whether they are
running from a checkout or an install to emit the right command and write to
the right place.

## Why

Two constraints force this:

- **`uvx` users need a stable, writable config home** outside the package
  cache, or the one-command install story is dead on the first `init`.
- **Existing checkout users (`uv run` from a clone) must not break** — their
  `connections/config/db.ini` is full of SSH keys and passwords; silently
  ignoring it on upgrade is the worst kind of breakage.

The search order is: (1) `$SQL_BUDDY_CONFIG` if set (power users, CI, multiple
configs), else (2) the per-user config dir via `platformdirs`
(`~/.config/sql-buddy/db.ini` on Linux, `%LOCALAPPDATA%\sql-buddy\sql-buddy\db.ini` on Windows,
`~/Library/Application Support/sql-buddy` on macOS), else (3) the
checkout-relative path `connections/config/db.ini` for dev/back-compat. New
`uvx` users land on (2); existing checkout users land on (3). The three
duplicated path resolutions collapse into a single `config_path()`.

Invocation-mode detection uses one reliable signal — does the source tree look
like a checkout (`connections/config/db.ini.example` present and writable, or a
`.git` nearby)? If yes, today's behavior (emit
`uv --directory . run server.py`, write config checkout-relative). If no,
default to installed mode: emit `uvx --from sqlbuddy-nwi sql-buddy`, write
config to the user dir. `--python` (existing) and a new `--command '<cmd>'` override the emitted
command for the `pipx`/`pip` minority.

## Considered options

- **Single `DATABASE_URL` env var** (the playbook's model): rejected — sql-buddy
  configures many **Connections** (one `db.ini` section each), not one flat
  database URL. A flat env schema cannot express `[ssh:*]` bastions,
  per-connection port mappings, or N named endpoints.
- **Single hard-coded new path** (`~/.config/sql-buddy/db.ini`, stop looking in
  checkout): rejected — silently ignores every existing checkout user's
  `db.ini` on upgrade.
- **`--mode {dev,uvx,installed}` flag**: rejected — always correct but pushes a
  flag onto every user and breaks the `uvx sql-buddy mcp add` one-liner.
  Auto-detect with an override reaches the same correctness with better default
  UX.
- **Always emit `uvx sql-buddy` post-publish**: rejected for `mcp add` —
  silently breaks dev users whose `.mcp.json` would then require PyPI/uvx
  instead of `uv run`.

## Consequences

- `platformdirs` becomes a runtime dependency (small, standard; the README's
  Windows support makes hand-rolling XDG/AppData/Library paths a bug farm).
- The back-compat checkout-relative path (3) is load-bearing: removing it later
  is a breaking change for dev users, so it stays indefinitely.
- `init`'s write target follows the same detection: checkout-relative in dev,
  user dir when installed.
- Runtime caches (query history, schema/info caches in `db_query.py`) follow the
  same pattern via `cache_dir_path()`: `$DB_QUERY_HISTORY_DIR` > per-user cache
  dir when installed > checkout-relative `database/` dir. Without this, an
  installed copy would crash on first query writing into the read-only package
  cache.
