"""sql-buddy command-line interface.

Single dispatcher behind the `sql-buddy` console script AND the subcommand
entry for `python server.py <cmd>`.

Subcommands:
  (none) | serve | stdio   run the MCP server over stdio (normal mode)
  version | --version | -V print version
  doctor [--connect]       validate install + db.ini (+ live pings if --connect)
  update-check             compare local checkout to the git remote
  mcp print [--python]     print the MCP client config block (paths filled in)
  mcp add [--client claude] [--scope project|user] [--python]
                           write the config block into a client's config file
  init                     run the interactive setup wizard
  help | --help | -h       show usage

Bare `sql-buddy` (or `python server.py` with no subcommand) starts the MCP
server, so existing MCP client configs keep working unchanged.
"""

import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# The Python version floor declared in pyproject.toml (requires-python).
_MIN_PY = (3, 11)

# Runtime dependencies doctor verifies are importable. These mirror
# pyproject.toml [project.dependencies] (plus the system `ssh` binary for
# Auto-mode tunnels, checked separately).
_RUNTIME_DEPS = [
    ("mcp", "mcp.server.fastmcp"),
    ("pymysql", None),
    ("clickhouse_driver", None),
    ("pandas", None),
]


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------

def get_version() -> str:
    """Installed dist version if available, else the bundled literal."""
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("sql-buddy")
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    try:
        from _version import __version__
        return __version__
    except Exception:
        return "0.0.0+unknown"


def cmd_version() -> int:
    print(get_version())
    return 0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def _check_python() -> tuple[str, bool]:
    ok = sys.version_info >= _MIN_PY
    return (f"Python {sys.version_info.major}.{sys.version_info.minor}"
            f".{sys.version_info.micro} (need >={_MIN_PY[0]}.{_MIN_PY[1]})", ok)


def _check_dep(dist: str, mod: str | None) -> tuple[str, bool]:
    import importlib
    target = mod or dist
    try:
        importlib.import_module(target)
        return f"dependency importable: {target}", True
    except Exception as e:
        return f"dependency MISSING: {target} ({e})", False


def _check_ssh() -> tuple[str, bool]:
    path = shutil.which("ssh")
    if path:
        return f"ssh binary on PATH: {path}", True
    return ("ssh binary NOT found on PATH (Auto-mode SSH tunnels will fail; "
            "install an OpenSSH client)", False)


def _parse_db_ini():
    """Return (configparser.ConfigParser, error_or_None). Stdlib only — no
    driver imports — so doctor stays lightweight and offline."""
    import configparser
    cfg_file = os.path.join(_HERE, "connections", "config", "db.ini")
    if not os.path.exists(cfg_file):
        return None, f"db.ini not found at {cfg_file} (run: sql-buddy init)"
    cfg = configparser.ConfigParser()
    try:
        cfg.read(cfg_file)
    except Exception as e:
        return None, f"db.ini failed to parse: {e}"
    return cfg, None


def _connection_names(cfg) -> list[str]:
    return [s for s in cfg.sections() if not s.lower().startswith("ssh:")]


def _check_config(cfg) -> list[tuple[str, bool, str]]:
    """Static validation of db.ini shape. Returns (label, ok, detail) rows."""
    rows: list[tuple[str, bool, str]] = []
    conns = _connection_names(cfg)
    if not conns:
        rows.append(("db.ini: no connections defined", False,
                     "add at least one [section] (see db.ini.example)"))
    ssh_blocks = [s for s in cfg.sections() if s.lower().startswith("ssh:")]
    for name in conns:
        tunneled = cfg.has_option(name, "tunnel") and cfg.get(name, "tunnel", fallback="").strip()
        issues = []
        ctype = cfg.get(name, "type", fallback="").strip().lower()
        if ctype and ctype not in ("clickhouse", "mysql"):
            issues.append(f"unknown type '{ctype}'")
        if tunneled:
            block = tunneled[4:] if tunneled.startswith("ssh:") else tunneled
            if f"ssh:{block}" not in cfg.sections():
                issues.append(f"tunnel references missing [ssh:{block}]")
            for req in ("remote_host", "remote_port", "local_port"):
                if not cfg.has_option(name, req):
                    issues.append(f"missing {req}")
        else:
            for req in ("host", "port"):
                if not cfg.has_option(name, req):
                    issues.append(f"missing {req}")
        if not cfg.has_option(name, "user"):
            issues.append("missing user")
        detail = ", ".join(issues) if issues else (
            f"{ctype or '?'}" + (f", tunnel={tunneled}" if tunneled else ", direct")
        )
        rows.append((f"connection [{name}]", not issues, detail))
    for block in ssh_blocks:
        for req in ("host", "user", "key"):
            if not cfg.has_option(block, req):
                rows.append((f"bastion [{block}]", False, f"missing {req}"))
    return rows


def _mcp_candidate_files() -> list[str]:
    home = os.path.expanduser("~")
    cands = [
        os.path.join(os.getcwd(), ".mcp.json"),
        os.path.join(os.getcwd(), ".claude", "settings.json"),
        os.path.join(home, ".claude.json"),
    ]
    if sys.platform == "darwin":
        cands.append(os.path.join(home, "Library", "Application Support",
                                  "Claude", "claude_desktop_config.json"))
    if sys.platform == "win32":
        appdata = os.environ.get("AppData")
        if appdata:
            cands.append(os.path.join(appdata, "Claude", "claude_desktop_config.json"))
    return cands


def _validate_mcp_entry(entry: dict) -> tuple[bool, str]:
    """Check one mcpServers entry resolves: command on PATH/exists AND every
    absolute path in its args is present. Returns (ok, detail).

    The arg-path check catches the two classic breakages: a `command` pointing at
    a deleted `.venv/bin/python`, and an `args` path (the repo dir / server.py)
    that moved after the config was written.
    """
    cmd = entry.get("command")
    args = entry.get("args") or []
    if not cmd:
        return False, "no 'command' set"
    if cmd in ("uv", "uvx", "npx", "node", "python", "python3"):
        if not shutil.which(cmd):
            return False, f"command '{cmd}' not found on PATH"
    elif os.path.isabs(cmd):
        if not os.path.exists(cmd):
            return False, f"command path MISSING: {cmd}"
    missing = [a for a in args
               if isinstance(a, str) and os.path.isabs(a) and not os.path.exists(a)]
    if missing:
        return False, f"arg path(s) MISSING: {', '.join(missing)}"
    return True, f"command '{cmd}' resolves"


def _check_mcp_config() -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    found_any = False
    for path in _mcp_candidate_files():
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            rows.append((f"MCP config {path}", False, f"unreadable: {e}"))
            continue
        servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
        if "sql-buddy" in servers:
            found_any = True
            ok, detail = _validate_mcp_entry(servers["sql-buddy"])
            rows.append((f"MCP config {path} [sql-buddy]", ok, detail))
        else:
            # also detect a server whose args mention server.py / sql-buddy
            for name, ent in servers.items():
                blob = json.dumps(ent).lower()
                if "sql-buddy" in blob or "server.py" in blob:
                    found_any = True
                    ok, detail = _validate_mcp_entry(ent)
                    rows.append((f"MCP config {path} [{name}]", ok, detail))
    if not found_any:
        rows.append(("MCP client config", False,
                     "sql-buddy not registered with any detected client "
                     "(run: sql-buddy mcp add)"))
    return rows


def _check_connection_live(name: str) -> tuple[str, bool, str]:
    try:
        import db_query
        raw = db_query.check_connection_query(name)
        res = json.loads(raw)
        if res.get("status") == "ok":
            return (f"connection [{name}] reachable", True,
                    f"{res.get('latency_ms')} ms")
        return (f"connection [{name}] error", False,
                str(res.get("error") or res))
    except Exception as e:
        return (f"connection [{name}] error", False, str(e))


def doctor(connect: bool = False) -> int:
    """Validate the install. Returns process exit code (0 all-pass, 1 any fail)."""
    print(f"sql-buddy {get_version()} doctor\n")
    fails = 0

    def emit(label, ok, detail=""):
        nonlocal fails
        mark = "OK  " if ok else "FAIL"
        if not ok:
            fails += 1
        line = f"  [{mark}] {label}"
        if detail:
            line += f"  ({detail})"
        print(line)

    # 1. runtime environment
    print("== runtime ==")
    lbl, ok = _check_python()
    emit(lbl, ok)
    emit(*_check_ssh())
    for dist, mod in _RUNTIME_DEPS:
        emit(*_check_dep(dist, mod))

    # 2. config
    print("\n== db.ini ==")
    cfg, err = _parse_db_ini()
    if err:
        emit("db.ini", False, err)
        cfg = None
    else:
        conns = _connection_names(cfg)
        emit(f"db.ini parsed ({len(conns)} connection(s))", True,
             os.path.join(_HERE, "connections", "config", "db.ini"))
        for label, ok, detail in _check_config(cfg):
            emit(label, ok, detail)

    # 3. mcp client config
    print("\n== MCP client config ==")
    for label, ok, detail in _check_mcp_config():
        emit(label, ok, detail)

    # 4. live connection pings (opt-in — may open tunnels / hit the network)
    if connect and cfg is not None:
        print("\n== live connections (SELECT 1) ==")
        for name in _connection_names(cfg):
            emit(*_check_connection_live(name))
    elif connect and cfg is None:
        print("\n== live connections skipped (no db.ini) ==")

    print(f"\n{'All checks passed.' if fails == 0 else f'{fails} check(s) failed.'}")
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# update-check
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str = _HERE) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", "git not installed"


def update_check() -> int:
    """Compare the local checkout HEAD to the upstream tracking branch tip."""
    rc, local, _ = _git(["rev-parse", "HEAD"])
    if rc != 0:
        print("Not a git checkout (or git unavailable) — cannot check for updates.")
        print("If you installed via a vendored bundle, re-download the latest "
              "release to update.")
        return 0
    print(f"local : {local[:12]}  sql-buddy {get_version()}")

    rc, upstream_ref, _ = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if rc != 0:
        # No upstream configured. Try origin/<current-branch> as a best effort.
        rc2, branch, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch or "master"
        rc, remote, err = _git(["ls-remote", "origin", branch])
        if rc != 0:
            print(f"Could not reach remote 'origin' ({err or 'offline'}). "
                  "Skipping remote comparison.")
            return 0
        remote_sha = (remote.split()[0] if remote else "").strip()
        upstream_label = f"origin/{branch}"
    else:
        upstream_label = upstream_ref
        # Fetch is intentionally skipped (no network mutation, works offline-ish
        # against already-known refs); query the remote tip directly.
        parts = upstream_ref.split("/", 1)
        remote_name, branch_name = (parts + [None])[:2] if len(parts) == 2 else ("origin", parts[0])
        rc, remote, err = _git(["ls-remote", remote_name, branch_name])
        if rc != 0:
            print(f"Could not reach '{upstream_label}' ({err or 'offline'}). "
                  "Skipping remote comparison.")
            return 0
        remote_sha = (remote.split()[0] if remote else "").strip()

    print(f"remote: {remote_sha[:12]}  {upstream_label}")
    if not remote_sha:
        print("Remote ref not found. Check your network or the branch name.")
        return 0
    if remote_sha == local:
        print("Up to date.")
        return 0
    print("Update available. Run:  git pull")
    print("(then `uv pip install -e .` if dependencies changed)")
    return 0


# ---------------------------------------------------------------------------
# mcp print / add
# ---------------------------------------------------------------------------

def _server_py() -> str:
    return os.path.join(_HERE, "server.py")


def _mcp_entry(python_mode: bool) -> dict:
    """Build the mcpServers['sql-buddy'] entry, paths auto-filled.

    Default (uv mode) uses `uv --directory <repo> run server.py` so the venv is
    auto-provisioned and there is no absolute .venv path to go stale. `--python`
    uses the interpreter currently running the CLI (sys.executable), which is
    the most robust absolute path when uv is unavailable.
    """
    if python_mode:
        return {"command": sys.executable, "args": [_server_py()]}
    return {"command": "uv", "args": ["--directory", _HERE, "run", "server.py"]}


def cmd_mcp_print(python_mode: bool) -> int:
    entry = _mcp_entry(python_mode)
    if not python_mode and not shutil.which("uv"):
        print("# note: 'uv' not found on PATH; this config requires it. "
              "Use --python for a sys.executable-based config.\n",
              file=sys.stderr)
    print(json.dumps({"mcpServers": {"sql-buddy": entry}}, indent=2))
    return 0


def _read_json_file(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _write_json_file(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _client_config_path(client: str, scope: str) -> str:
    client = (client or "claude").lower()
    scope = (scope or "project").lower()
    if client != "claude":
        raise SystemExit(f"unsupported client '{client}' (only 'claude' for now)")
    home = os.path.expanduser("~")
    if scope == "project":
        return os.path.join(os.getcwd(), ".mcp.json")
    if scope == "user":
        return os.path.join(home, ".claude.json")
    raise SystemExit(f"unsupported scope '{scope}' (use 'project' or 'user')")


def cmd_mcp_add(python_mode: bool, client: str, scope: str) -> int:
    path = _client_config_path(client, scope)
    entry = _mcp_entry(python_mode)
    mode_note = "python (sys.executable)" if python_mode else "uv (auto-provisions venv)"
    if os.path.exists(path):
        try:
            data = _read_json_file(path)
            if not isinstance(data, dict):
                raise ValueError("top-level JSON is not an object")
        except Exception as e:
            raise SystemExit(f"{path} exists but is not valid JSON ({e}); "
                             "fix or remove it first")
        bak = path + ".bak"
        i = 0
        while os.path.exists(bak):
            i += 1
            bak = f"{path}.bak{i}"
        shutil.copy2(path, bak)
        print(f"Backed up existing config to {bak}")
    else:
        data = {}
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SystemExit(f"{path} has 'mcpServers' that is not an object; fix it first")
    servers["sql-buddy"] = entry
    _write_json_file(path, data)
    print(f"Wrote sql-buddy into {path}  (scope: {scope}, mode: {mode_note})")
    print("Restart your MCP client to pick it up.")
    return 0


# ---------------------------------------------------------------------------
# help / dispatcher
# ---------------------------------------------------------------------------

_USAGE = """\
sql-buddy {ver}  — read-only MCP database server

Usage:
  sql-buddy [serve|stdio]        run the MCP server over stdio (default)
  sql-buddy version              print version
  sql-buddy doctor [--connect]   validate install + db.ini (+ live pings)
  sql-buddy update-check         compare local checkout to the git remote
  sql-buddy mcp print [--python] print the MCP client config block
  sql-buddy mcp add [--client claude] [--scope project|user] [--python]
                                 write the config into a client's config file
  sql-buddy init                 interactive setup wizard
  sql-buddy help                 show this help

Bare `sql-buddy` (no args) starts the MCP server, so MCP client configs that
invoke `sql-buddy` or `python server.py` keep working unchanged.
"""


def cmd_help() -> int:
    print(_USAGE.format(ver=get_version()))
    return 0


def _run_server() -> int:
    # Lazy import: keeps subcommands lightweight and avoids importing the MCP
    # runtime / DB drivers when the user only asked for `version` or `doctor`.
    import server
    server.run_server()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("serve", "server", "stdio"):
        return _run_server()

    cmd, rest = args[0], args[1:]

    if cmd in ("version", "--version", "-V"):
        return cmd_version()
    if cmd in ("help", "--help", "-h", "-?"):
        return cmd_help()
    if cmd == "doctor":
        connect = "--connect" in rest
        return doctor(connect=connect)
    if cmd == "update-check":
        return update_check()
    if cmd == "init":
        import init
        init.main()
        return 0
    if cmd == "mcp":
        if not rest:
            print("Usage: sql-buddy mcp <print|add> [...]", file=sys.stderr)
            return 2
        sub = rest[0]
        flags = rest[1:]
        python_mode = "--python" in flags
        if sub == "print":
            return cmd_mcp_print(python_mode)
        if sub == "add":
            client = _flag_value(flags, "--client", default="claude")
            scope = _flag_value(flags, "--scope", default="project")
            return cmd_mcp_add(python_mode, client=client, scope=scope)
        print(f"Unknown 'mcp' subcommand: {sub} (want print|add)", file=sys.stderr)
        return 2

    print(f"Unknown command: {cmd}", file=sys.stderr)
    cmd_help()
    return 2


def _flag_value(flags: list[str], name: str, default: str | None = None) -> str | None:
    """Parse `--name value` or `--name=value` from a flat flag list."""
    for i, f in enumerate(flags):
        if f == name and i + 1 < len(flags):
            return flags[i + 1]
        if f.startswith(name + "="):
            return f.split("=", 1)[1]
    return default


if __name__ == "__main__":
    sys.exit(main())
