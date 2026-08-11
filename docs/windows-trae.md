# Windows & Trae IDE

The short install path in the [root `README.md`](../README.md) works on Windows
with Claude Code. This page covers the **Windows + [Trae](https://www.trae.ai/)**
(or Trae CN) path, which needs a few extra steps — Trae's MCP host is pickier
than a shell about PATH, spaces in `command`, and cold-start timeouts.

---

## One-time setup

1. **Install [uv](https://docs.astral.sh/uv/)** in a path **without spaces**:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Reopen the terminal; confirm `uv --version`.
2. **OpenSSH Client** only if you want **Auto** tunnels (Settings → Apps →
   Optional features). Many Windows users keep an `ssh -N …` window open instead
   (**External** mode).
3. Clone and smoke-test:
   ```powershell
   git clone <this-repo-url> sql-buddy
   cd sql-buddy
   uv run server.py          # Ctrl+C once it stays quiet
   uv run sql-buddy init
   uv run sql-buddy doctor
   ```

---

## Wire Trae

```powershell
uv run sql-buddy mcp add --client trae --scope user   # user-global (every Trae window)
uv run sql-buddy mcp add --client trae                # project-only (.trae/mcp.json)
```

Trae also gets longer start/run timeouts and UTF-8 env (so JSON-RPC survives
Chinese Windows code pages). Fully restart Trae (not just reload). Check
**Settings → MCP** — sql-buddy should be green. On failure, open **Output →
MCP Server Host** (`Ctrl+Shift+U`) for the spawn log.

---

## Windows gotchas we already handle

| Symptom | What sql-buddy does |
|--------|---------------------------|
| Trae can't find `uv` | `mcp add` resolves `uv` to an **absolute** `.exe` path; falls back to `sys.executable` if missing |
| `command` has spaces (Trae rejects it) | Prefers the `py` launcher, or refuses to write a broken config |
| First start times out | Trae entries set `START_MCP_TIMEOUT_MS=120000` |
| Garbled errors / JSON parse fails on CN Windows | Sets `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` |
| Flashing black console when a tunnel opens | SSH subprocess uses `CREATE_NO_WINDOW` |
| `ssh` missing in Auto mode | `doctor` tells you to install OpenSSH Client |
| Key path `~\.ssh\id_ed25519` | Expanded + normalized on write and at tunnel start |
| `db.ini` encoding (GBK editors) | Reader accepts UTF-8 BOM / UTF-8 / GBK / mbcs |

---

## External tunnel on Windows (common)

If Auto SSH is awkward, run the tunnel yourself and point sql-buddy at localhost:

```powershell
ssh -N -L 127.0.0.1:10021:10.0.0.21:9000 -p 22222 user@bastion -i $HOME\.ssh\id_ed25519   # keep open
```

```ini
# connections/config/db.ini  (External — no tunnel= key)
[ck]
type     = clickhouse
host     = localhost
port     = 10021
user     = readonly
password = ...
database = default
```

Then `uv run sql-buddy doctor --connect`.
