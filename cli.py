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
import stat
import subprocess
import sys
import tempfile

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


def _has_tunnel(cfg) -> bool:
    """True if any connection declares a self-managed SSH tunnel."""
    if cfg is None:
        return False
    for name in _connection_names(cfg):
        val = cfg.get(name, "tunnel", fallback="").strip()
        if val:
            return True
    return False


def _check_ssh(needed: bool) -> tuple[str, str, str]:
    """Return (label, severity, detail). severity in {'ok','warn','fail'}.

    `needed` says whether ANY configured connection actually uses a self-managed
    tunnel. ssh is only required in that case, so a missing binary is a hard
    failure only when a tunnel is configured; otherwise it's a non-blocking warn
    (e.g. an External/direct-only setup never needs ssh).
    """
    path = shutil.which("ssh")
    if path:
        return "ssh binary", "ok", f"on PATH: {path}"
    if needed:
        return ("ssh binary", "fail",
                "NOT on PATH but a connection uses a self-managed tunnel "
                "(Auto mode); install an OpenSSH client")
    return ("ssh binary", "warn",
            "not on PATH (only needed for Auto-mode tunnels — fine for "
            "External/direct-only setups)")


def _parse_db_ini():
    """Return (configparser, error_or_None).

    Uses the shared `connections.config_util.read_config` reader (UTF-8-BOM /
    UTF-8 / GBK, same as the runtime) so doctor agrees with what the server
    will actually load. Never echoes the raw parse exception — ConfigParser
    errors embed the offending source line, which on db.ini can be a password.
    """
    cfg_file = os.path.join(_HERE, "connections", "config", "db.ini")
    if not os.path.exists(cfg_file):
        return None, f"db.ini not found at {cfg_file} (run: sql-buddy init)"
    try:
        from connections.config_util import read_config
        return read_config(cfg_file), None
    except Exception:
        return None, ("db.ini failed to parse (syntax/encoding error; the "
                      "offending line is hidden to avoid leaking secrets)")


def _connection_names(cfg) -> list[str]:
    return [s for s in cfg.sections() if not s.lower().startswith("ssh:")]


_VALID_TYPES = frozenset({"clickhouse", "mysql", "mariadb"})


def _port_ok(raw: str) -> bool:
    """True if raw is an integer port in 1..65535 (runtime does int(port))."""
    try:
        p = int(str(raw).strip())
    except (TypeError, ValueError):
        return False
    return 1 <= p <= 65535


def _resolve_type_like_runtime(cfg, name: str) -> tuple[str | None, str | None]:
    """Mirror connections.manager.connection_type (without importing manager).

    Returns (ctype, error). error is set when runtime would raise ValueError
    (no explicit type and name prefix doesn't match ck*/db*/mysql*).
    """
    explicit = cfg.get(name, "type", fallback="").strip().lower()
    if explicit:
        if explicit not in _VALID_TYPES:
            return None, f"unknown type '{explicit}' (want clickhouse|mysql|mariadb)"
        return explicit, None
    if name.startswith("ck"):
        return "clickhouse", None
    if name.startswith("db") or name.startswith("mysql"):
        return "mysql", None
    return None, "missing type (set type=clickhouse|mysql, or name the section ck*/db*/mysql*)"


def _check_config(cfg) -> list[tuple[str, bool, str]]:
    """Static validation of db.ini shape. Returns (label, ok, detail) rows.

    Mirrors runtime rules: connection_type() resolution + int(port) conversion.
    """
    rows: list[tuple[str, bool, str]] = []
    conns = _connection_names(cfg)
    if not conns:
        rows.append(("db.ini: no connections defined", False,
                     "add at least one [section] (see db.ini.example)"))
    ssh_blocks = [s for s in cfg.sections() if s.lower().startswith("ssh:")]
    for name in conns:
        tunneled = cfg.has_option(name, "tunnel") and cfg.get(name, "tunnel", fallback="").strip()
        issues = []
        ctype, type_err = _resolve_type_like_runtime(cfg, name)
        if type_err:
            issues.append(type_err)
        if tunneled:
            block = tunneled[4:] if tunneled.startswith("ssh:") else tunneled
            if f"ssh:{block}" not in cfg.sections():
                issues.append(f"tunnel references missing [ssh:{block}]")
            for req in ("remote_host", "remote_port", "local_port"):
                if not cfg.has_option(name, req):
                    issues.append(f"missing {req}")
            for pkey in ("remote_port", "local_port"):
                if cfg.has_option(name, pkey) and not _port_ok(cfg.get(name, pkey)):
                    issues.append(f"{pkey} must be integer 1..65535")
        else:
            for req in ("host", "port"):
                if not cfg.has_option(name, req):
                    issues.append(f"missing {req}")
            if cfg.has_option(name, "port") and not _port_ok(cfg.get(name, "port")):
                issues.append("port must be integer 1..65535")
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
        if cfg.has_option(block, "port") and not _port_ok(cfg.get(block, "port")):
            rows.append((f"bastion [{block}]", False, "port must be integer 1..65535"))
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

    Every non-absolute command is checked with shutil.which (not an allowlist) —
    a bare name like `sql-buddy` or a typo must fail, not silently "resolve".
    Absolute / relative path commands are checked on the filesystem.
    """
    if not isinstance(entry, dict):
        return False, f"entry must be an object, got {type(entry).__name__}"
    cmd = entry.get("command")
    args = entry.get("args", [])
    if args is None:
        args = []
    if not isinstance(args, list):
        return False, f"'args' must be a list, got {type(args).__name__}"
    if not cmd or not isinstance(cmd, str):
        return False, "no 'command' set"
    if os.path.isabs(cmd) or os.sep in cmd or (os.altsep and os.altsep in cmd):
        # Absolute or relative filesystem path
        if not os.path.exists(cmd):
            return False, f"command path MISSING: {cmd}"
    else:
        # Bare executable name — must be on PATH
        if not shutil.which(cmd):
            return False, f"command '{cmd}' not found on PATH"
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
        if not isinstance(data, dict):
            rows.append((f"MCP config {path}", False, "top-level JSON is not an object"))
            continue
        servers = data.get("mcpServers", {})
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            rows.append((f"MCP config {path}", False,
                         f"'mcpServers' must be an object, got {type(servers).__name__}"))
            continue
        if "sql-buddy" in servers:
            found_any = True
            ent = servers["sql-buddy"]
            if not isinstance(ent, dict):
                rows.append((f"MCP config {path} [sql-buddy]", False,
                             f"entry must be an object, got {type(ent).__name__}"))
            else:
                ok, detail = _validate_mcp_entry(ent)
                rows.append((f"MCP config {path} [sql-buddy]", ok, detail))
        else:
            # also detect a server whose args mention server.py / sql-buddy
            for name, ent in servers.items():
                if not isinstance(ent, dict):
                    continue
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


def _live_hint(msg: str) -> str:
    """Actionable hint for a live-Connection failure. Mirrors server._hint_for
    without coupling doctor to the MCP runtime."""
    low = msg.lower()
    if any(k in low for k in ("refused", "timed out", "timeout", "tunnel",
                              "connect", "socket", " eof", "closed")):
        return (f"{msg} | hint: transport/tunnel error — in External mode the "
                "user's tunnel may be down; in Auto mode check remote_host/"
                "remote_port (the DB as seen from the bastion)")
    if any(k in low for k in ("access denied", "authentication", "auth",
                              "password", "login")):
        return f"{msg} | hint: DB auth failed — check user/password in db.ini"
    if "no section" in low or "unknown connection" in low:
        return (f"{msg} | hint: unknown connection — run sql-buddy doctor to "
                "list configured names")
    return msg


def _check_connection_live(name: str) -> tuple[str, str, str]:
    """Ping one Connection (SELECT 1). Returns (label, severity, detail)."""
    try:
        # Match server startup: load .env + install secret filters before any
        # db_query / connections import so $env: refs in db.ini resolve.
        import db_security
        db_security.install()
        import db_query
        raw = db_query.check_connection_query(name)
        res = json.loads(raw)
        if res.get("status") == "ok":
            return (f"connection [{name}] reachable", "ok",
                    f"{res.get('latency_ms')} ms")
        return (f"connection [{name}] error", "fail",
                str(res.get("error") or res))
    except Exception as e:
        return (f"connection [{name}] error", "fail", _live_hint(str(e)))


def doctor(connect: bool = False) -> int:
    """Validate the install. Returns process exit code (0 unless something FAILs;
    WARNs never fail)."""
    print(f"sql-buddy {get_version()} doctor\n")
    fails = 0
    warns = 0

    def emit(label, severity, detail=""):
        nonlocal fails, warns
        if severity == "ok":
            mark = "OK  "
        elif severity == "warn":
            mark = "WARN"
            warns += 1
        else:
            mark = "FAIL"
            fails += 1
        line = f"  [{mark}] {label}"
        if detail:
            line += f"  ({detail})"
        print(line)

    # Parse db.ini once, up front, so the ssh check can be conditional on
    # whether any connection actually uses a self-managed tunnel.
    cfg, err = _parse_db_ini()

    # 1. runtime environment
    print("== runtime ==")
    lbl, ok = _check_python()
    emit(lbl, "ok" if ok else "fail")
    emit(*_check_ssh(needed=_has_tunnel(cfg)))
    for dist, mod in _RUNTIME_DEPS:
        d_label, d_ok = _check_dep(dist, mod)
        emit(d_label, "ok" if d_ok else "fail")

    # 2. config
    print("\n== db.ini ==")
    if err:
        emit("db.ini", "fail", err)
        cfg = None
    else:
        conns = _connection_names(cfg)
        emit(f"db.ini parsed ({len(conns)} connection(s))", "ok",
             os.path.join(_HERE, "connections", "config", "db.ini"))
        for label, ok, detail in _check_config(cfg):
            emit(label, "ok" if ok else "fail", detail)

    # 3. mcp client config
    print("\n== MCP client config ==")
    for label, ok, detail in _check_mcp_config():
        emit(label, "ok" if ok else "fail", detail)

    # 4. live Connection pings (opt-in — may open tunnels / hit the network)
    if connect and cfg is not None:
        print("\n== live connections (SELECT 1) ==")
        for name in _connection_names(cfg):
            emit(*_check_connection_live(name))
    elif connect and cfg is None:
        print("\n== live connections skipped (no db.ini) ==")

    summary = ", ".join([f"{fails} failed", f"{warns} warning(s)"])
    print(f"\n{'All checks passed.' if fails == 0 and warns == 0 else summary}")
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# update-check
# ---------------------------------------------------------------------------

def _redact_secrets(text: str) -> str:
    """Strip credential-bearing URL userinfo (and similar) from diagnostic text.

    Git stderr often embeds the remote URL; if the remote is an HTTPS URL with
    an embedded token (`https://user:TOKEN@host/...`), echoing raw stderr would
    leak it. Replace userinfo with `***`.
    """
    if not text:
        return text
    import re
    # https://user:pass@host  or  https://token@host
    text = re.sub(r"(https?://)([^/@\s]+)@", r"\1***@", text)
    # ssh-style with embedded password is rare; still strip user:pass@host
    text = re.sub(r"(\b)([^:\s/]+):([^@\s/]+)@", r"\1***@", text)
    return text


def _git(args: list[str], cwd: str = _HERE) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
        return p.returncode, p.stdout.strip(), _redact_secrets(p.stderr.strip())
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

    remote_name, branch_name = "origin", "master"
    rc, upstream_ref, _ = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if rc != 0:
        # No upstream configured. Try origin/<current-branch> as a best effort.
        rc2, branch, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        branch_name = branch or "master"
        remote_name = "origin"
        upstream_label = f"origin/{branch_name}"
    else:
        upstream_label = upstream_ref
        parts = upstream_ref.split("/", 1)
        if len(parts) == 2:
            remote_name, branch_name = parts
        else:
            remote_name, branch_name = "origin", parts[0]

    # Never print raw git stderr: it can embed credential-bearing remote URLs
    # (https://user:TOKEN@host/...) even after partial redaction.
    rc, remote, _err = _git(["ls-remote", remote_name, branch_name])
    if rc != 0:
        print(f"Could not reach '{upstream_label}' (offline or auth failed). "
              "Skipping remote comparison. Try: git fetch")
        return 0
    remote_sha = (remote.split()[0] if remote else "").strip()

    print(f"remote: {remote_sha[:12] if remote_sha else '(none)'}  {upstream_label}")
    if not remote_sha:
        print("Remote ref not found. Check your network or the branch name.")
        return 0
    if remote_sha == local:
        print("Up to date.")
        return 0

    # ls-remote only returns a SHA — the object may not be in the local store.
    # merge-base needs the object; fetch the tip if missing, otherwise ancestry
    # checks exit 128 and we'd mis-report "diverged".
    rc_has, _, _ = _git(["cat-file", "-e", f"{remote_sha}^{{commit}}"])
    if rc_has != 0:
        rc_f, _, _err_f = _git(["fetch", "--quiet", remote_name, branch_name])
        if rc_f != 0:
            print(f"Remote tip {remote_sha[:12]} is not in local history and "
                  f"fetch failed. Run: git fetch")
            return 0
        # re-check after fetch
        rc_has2, _, _ = _git(["cat-file", "-e", f"{remote_sha}^{{commit}}"])
        if rc_has2 != 0:
            print(f"Fetched {upstream_label} but still missing object "
                  f"{remote_sha[:12]}. Run: git fetch {remote_name}")
            return 0

    # Distinguish behind / ahead / diverged via commit ancestry.
    # `merge-base --is-ancestor A B` exits 0 when A is reachable from B.
    rc_local_in_remote, _, _ = _git(
        ["merge-base", "--is-ancestor", "HEAD", remote_sha])
    rc_remote_in_local, _, _ = _git(
        ["merge-base", "--is-ancestor", remote_sha, "HEAD"])
    # exit 128 = git error (still-missing object, corrupt repo, …) — don't
    # pretend we know the relationship.
    if rc_local_in_remote == 128 or rc_remote_in_local == 128:
        print("Could not compare ancestry. Run: git fetch && git status")
        return 0
    if rc_local_in_remote == 0:
        print("Update available. Run:  git pull")
        print("(then `uv pip install -e .` if dependencies changed)")
    elif rc_remote_in_local == 0:
        print("Local is ahead of remote (unpushed commits). No update needed.")
    else:
        print("Branches have diverged. Run:  git pull --rebase (or merge) and resolve.")
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
    """Write JSON atomically, preserving the existing file mode.

    MCP client configs (notably ~/.claude.json) are commonly mode 0600 and may
    hold third-party API tokens. Writing through a naive temp file would reset
    the mode to the process umask (often 0644), exposing them to other local
    users. So: inherit the existing mode if the file exists, else create 0600;
    and use a 0600 temp file so the data is never world-readable mid-write.
    """
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    mode = 0o600
    if os.path.exists(path):
        mode = stat.S_IMODE(os.stat(path).st_mode)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".sql-buddy-mcp-")
    try:
        os.chmod(tmp, mode)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


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
        # Pass `rest` explicitly: init.main() uses argparse, which reads
        # sys.argv — so we must not leave the literal "init" token in argv or
        # argparse rejects it as an unrecognized argument.
        return init.main(rest) or 0
    if cmd == "mcp":
        if not rest:
            print("Usage: sql-buddy mcp <print|add> [--python] "
                  "[--client claude] [--scope project|user]", file=sys.stderr)
            return 2
        sub = rest[0]
        flags = rest[1:]
        try:
            parsed = _parse_mcp_flags(flags)
        except SystemExit as e:
            print(str(e) or "bad flags", file=sys.stderr)
            return 2
        if sub == "print":
            return cmd_mcp_print(parsed["python"])
        if sub == "add":
            return cmd_mcp_add(parsed["python"],
                               client=parsed["client"], scope=parsed["scope"])
        print(f"Unknown 'mcp' subcommand: {sub} (want print|add)", file=sys.stderr)
        return 2

    print(f"Unknown command: {cmd}", file=sys.stderr)
    cmd_help()
    return 2


def _parse_mcp_flags(flags: list[str]) -> dict:
    """Strict flag parser for `mcp print|add`. Unknown / valueless flags error."""
    out = {"python": False, "client": "claude", "scope": "project"}
    known = {"--python", "--client", "--scope"}
    i = 0
    while i < len(flags):
        f = flags[i]
        if f == "--python":
            out["python"] = True
            i += 1
            continue
        if f.startswith("--client=") or f.startswith("--scope="):
            name, _, val = f.partition("=")
            key = name[2:]  # strip --
            if not val:
                raise SystemExit(f"{name} requires a value")
            out[key] = val
            i += 1
            continue
        if f in ("--client", "--scope"):
            if i + 1 >= len(flags) or flags[i + 1].startswith("-"):
                raise SystemExit(f"{f} requires a value (got end of args)")
            out[f[2:]] = flags[i + 1]
            i += 2
            continue
        if f.startswith("-"):
            raise SystemExit(f"unknown flag: {f} (want {', '.join(sorted(known))})")
        raise SystemExit(f"unexpected argument: {f}")
    if out["scope"] not in ("project", "user"):
        raise SystemExit(f"unsupported --scope '{out['scope']}' (want project|user)")
    if out["client"] != "claude":
        raise SystemExit(f"unsupported --client '{out['client']}' (only 'claude' for now)")
    return out


if __name__ == "__main__":
    sys.exit(main())
