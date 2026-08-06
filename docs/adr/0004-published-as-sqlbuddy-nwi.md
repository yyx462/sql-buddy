# Published as `sqlbuddy-nwi` because `sql-buddy` was taken on PyPI

The PyPI distribution name is `sqlbuddy-nwi`, while the console-script command
(`sql-buddy`), the `mcpServers` key, the `FastMCP` server name, the per-user
config dir (`~/.config/sql-buddy`), and the GitHub repo all remain `sql-buddy`.
The divergence is forced: `sql-buddy` on PyPI is an unrelated SQL study-aid
package (Sammy Lee, v1.0.4, 2020), and PyPI names are first-come and immutable.

## Why a suffix rather than the clean name

- `-nwi` namespaces the package to the author (nwi), avoiding collision or
  confusion with the existing project while keeping the human-facing command
  unchanged.
- The command name (`sql-buddy`) is what users type and what every existing
  `.mcp.json` references; keeping it stable avoids breaking installed users.
  Only the pip/uvx *package* identifier changes.

## Considered options

- `sql-buddy-mcp` / `sqlbuddy` / `sql-buddy-cli` (all verified free): viable;
  `-nwi` chosen to personal-namespace rather than describe, since the
  descriptive names remain available for a later rename if this ever broadens
  beyond a single author.
- Rename the console script to match the dist: rejected — it breaks every
  existing `.mcp.json` that invokes `sql-buddy` and gains nothing (uvx still
  needs `--from` because the dist name still wouldn't be the literal `sql-buddy`).

## Consequences

- uvx needs `--from`: `uvx --from sqlbuddy-nwi sql-buddy` (package name !=
  command name). `mcp add` emits this automatically, so end users rarely type
  it; `pip install sqlbuddy-nwi` then `sql-buddy` is the clean path.
- `cli.get_version()` looks up `importlib.metadata.version("sqlbuddy-nwi")`,
  not `"sql-buddy"`.
- The PyPI name is sticky: renaming a published package is painful (no native
  rename; you publish the new name and yank the old), so this name is
  effectively permanent once v0.3.1 ships.
