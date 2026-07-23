#!/usr/bin/env python3
"""sql-buddy interactive setup wizard.

Asks how you reach your databases and writes `connections/config/db.ini` in
the right shape:

  - Auto     : sql-buddy opens the SSH tunnel itself. You give a bastion + key
               file + per-DB remote mapping. Connections get `tunnel = ssh:*`.
  - External : you already run a tunnel/connection elsewhere (e.g. an `ssh -N`
               terminal on Windows, or a DB exposed locally). sql-buddy just
               connects to a local host:port. No `tunnel` keys, so the
               self-managed tunnel code stays dormant.

Run:  python init.py        (or, after `uv pip install -e .`:  sql-buddy-init)
"""

import configparser
import json
import os
import shutil
import sys
from getpass import getpass

CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'connections', 'config', 'db.ini'
)


# --- WSL detection (lightweight, no heavy imports) ------------------------

def _is_wsl() -> bool:
    try:
        with open('/proc/version') as f:
            return 'microsoft' in f.read().lower()
    except OSError:
        return False


# --- prompt helpers -------------------------------------------------------

def _ask(prompt: str, default: str = None, optional: bool = False) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"{prompt}{suffix}: ").strip()
        if val:
            return val
        if default is not None:
            return default
        if optional:
            return ""
        print("  (required - please answer)")


def _ask_int(prompt: str, default: int = None) -> int:
    while True:
        raw = _ask(prompt, str(default) if default is not None else None)
        try:
            return int(raw)
        except ValueError:
            print("  (must be an integer)")


def _ask_password(prompt: str) -> str:
    try:
        return getpass(f"{prompt}: ")
    except EOFError:
        return ""


def _yes_no(prompt: str, default: bool = False) -> bool:
    raw = input(f"{prompt} [{'y' if default else 'n'}]: ").strip().lower()
    if not raw:
        return default
    return raw.startswith('y')


# --- mode selection -------------------------------------------------------

def _choose_mode() -> int:
    print(
        "\nHow do you reach your databases?\n"
        "  1) Auto     - sql-buddy opens the SSH tunnel itself\n"
        "                (you provide a bastion/jump-host + a private key file)\n"
        "  2) External - you already have a tunnel/connection running\n"
        "                (e.g. an `ssh -N` terminal on Windows, or a local DB)\n"
        "                sql-buddy just connects to a local host:port"
    )
    while True:
        c = input("\nChoose [1/2]: ").strip()
        if c in ('1', '2'):
            return int(c)
        print("  (enter 1 or 2)")


# --- bastion (auto mode) --------------------------------------------------

def _ask_bastion(cfg: configparser.RawConfigParser) -> str:
    print("\n--- Bastion / jump-host ---")
    if sys.platform == "win32" and not shutil.which("ssh"):
        print(
            "  note: `ssh` not on PATH. Auto mode needs OpenSSH Client\n"
            "  (Windows Settings → Apps → Optional features → OpenSSH Client).\n"
            "  Or pick External mode and run `ssh -N` yourself."
        )
    name = _ask("bastion name", "prod")
    section = f"ssh:{name}"
    cfg.add_section(section)
    cfg.set(section, "host", _ask("SSH host"))
    cfg.set(section, "port", str(_ask_int("SSH port", 22)))
    cfg.set(section, "user", _ask("SSH user"))
    key_default = os.path.join("~", ".ssh", "id_ed25519")
    key_raw = _ask(f"Private key path (e.g. {key_default})")
    # Expand ~ and normalize slashes so Windows paths like
    # C:\Users\…\.ssh\id_ed25519 and ~/ .ssh/… both work at runtime.
    key = os.path.normpath(os.path.expanduser(key_raw.strip().strip('"')))
    cfg.set(section, "key", key)
    cfg.set(section, "server_alive_interval", str(_ask_int("ServerAliveInterval (seconds)", 30)))
    cfg.set(section, "server_alive_count_max", str(_ask_int("ServerAliveCountMax", 3)))
    return name


def _default_remote_port(ctype: str) -> int:
    return 9000 if ctype == 'clickhouse' else 3306


def _add_connection_auto(cfg: configparser.RawConfigParser, block: str):
    print("\n--- Database connection ---")
    name = _ask("Connection name (e.g. ck, mysql)")
    cfg.add_section(name)
    ctype = _ask("Type [clickhouse/mysql]", "clickhouse")
    cfg.set(name, "type", ctype)
    cfg.set(name, "tunnel", f"ssh:{block}")
    cfg.set(name, "remote_host", _ask("Remote DB host (as seen FROM the bastion)"))
    cfg.set(name, "remote_port", str(_ask_int("Remote DB port", _default_remote_port(ctype))))
    cfg.set(name, "local_port", str(_ask_int("Local port (on THIS machine, must be free)")))
    cfg.set(name, "user", _ask("DB user (read-only recommended)"))
    cfg.set(name, "password", _ask_password("DB password"))
    db = _ask("Default database", optional=True)
    if db:
        cfg.set(name, "database", db)


# --- external mode: edit db.ini by hand -----------------------------------
# External config is simple (host/port/user/password) and often already exists,
# so we don't prompt for it - we point the user at db.ini to edit directly.
# Pasting passwords with special chars into a prompt is error-prone; a file edit
# is safer and lets the user reuse values they already have.

_EXTERNAL_TEMPLATE = """\
# db.ini - EXTERNAL tunnel mode. NEVER commit this file (gitignored).
#
# You run the SSH tunnel/connection elsewhere (e.g. `ssh -N` on Windows);
# sql-buddy connects to host:port below directly. No `tunnel` keys here.
# Add one [section] per database. See db.ini.example for the full format.

[ck]
type     = clickhouse
host     = localhost          # local end of your external tunnel
port     = 9000               # LOCAL tunnel port (not the remote DB port)
user     = your_readonly_user
password = your_password
database = default

# Example MySQL connection - copy & rename to add more:
# [mysql]
# type     = mysql
# host     = localhost
# port     = 3306
# user     = your_readonly_user
# password = your_password
# database = myapp
"""

_EXTERNAL_HELP = (
    "Edit db.ini: one [section] per database, with host/port/user/password\n"
    "  (and `database` for ClickHouse).\n"
    "  - host: local end of your external tunnel (usually localhost)\n"
    "  - port: the LOCAL port your tunnel forwards (not the remote DB port)\n"
    f"See {os.path.basename(CONFIG_FILE)}.example for the full format."
)

_WSL_HINT = (
    "WSL detected: sql-buddy auto-rewrites `host` to the Windows host IP, so your\n"
    "Windows-side tunnels must bind 0.0.0.0 (your `-L 0.0.0.0:...` already does)."
)

_VERIFY_HINT = (
    "Next, verify with:\n"
    "  python -c \"import db_query; print(db_query.list_connections())\"\n"
    "  python -c \"import db_query; print(db_query.execute_limit_query('SELECT 1','<conn>'))\""
)


def _write_external_template():
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        f.write(_EXTERNAL_TEMPLATE)


# --- write ----------------------------------------------------------------

def _backup_existing() -> str | None:
    """Copy current db.ini to a 0600 backup next to it (gitignored as db.ini.bak*)."""
    if not os.path.exists(CONFIG_FILE):
        return None
    n = 1
    while os.path.exists(f"{CONFIG_FILE}.bak{n}"):
        n += 1
    bak = f"{CONFIG_FILE}.bak{n}"
    shutil.copy2(CONFIG_FILE, bak)
    try:
        os.chmod(bak, 0o600)
    except OSError:
        pass
    return bak


def _setup_auto():
    cfg = configparser.RawConfigParser()
    block = _ask_bastion(cfg)
    _add_connection_auto(cfg, block)
    while _yes_no("\nAdd another connection?", default=False):
        _add_connection_auto(cfg, block)
    _write_cfg(cfg, "auto - self-managed SSH tunnels")


def _write_cfg(cfg, mode_label: str):
    """Back up any existing db.ini, then write `cfg` to CONFIG_FILE at mode 0600.

    Shared by the interactive and non-interactive paths so they can never
    drift in how they persist + verify. Mode 0600 so plaintext DB passwords
    are never world-readable under a permissive umask.
    """
    import stat
    import tempfile
    bak = _backup_existing()
    d = os.path.dirname(CONFIG_FILE)
    os.makedirs(d, exist_ok=True)
    # Preserve existing mode if already strict; otherwise force 0600.
    mode = 0o600
    if os.path.exists(CONFIG_FILE):
        mode = stat.S_IMODE(os.stat(CONFIG_FILE).st_mode) | 0o600
        # never widen beyond owner-only for a credential file
        mode = mode & 0o700
        if mode == 0:
            mode = 0o600
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".db.ini.")
    try:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # Windows: chmod is best-effort
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            cfg.write(f)
        os.replace(tmp, CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    print(f"\nWrote {CONFIG_FILE}  (mode: {mode_label}, perms 0600)")
    if bak:
        print(f"Previous config backed up to {bak}")
    print("\n" + _VERIFY_HINT)


def _setup_external():
    # Don't prompt for connection details - external config is simple and often
    # already exists. Point the user at db.ini to edit by hand instead.
    if os.path.exists(CONFIG_FILE):
        print(f"\nYou already have a db.ini - edit it directly:\n  {CONFIG_FILE}")
    else:
        _write_external_template()
        print(f"\nWrote a starter db.ini - edit it to fill in your connections:\n  {CONFIG_FILE}")
    print("\n" + _EXTERNAL_HELP)
    if _is_wsl():
        print("\n" + _WSL_HINT)
    print("\n" + _VERIFY_HINT)


def main(argv: list[str] | None = None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="sql-buddy-init",
        description="sql-buddy setup wizard — writes connections/config/db.ini",
    )
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="write db.ini from a JSON spec without prompting (for agents / CI)",
    )
    # No --json '<inline>': argv is visible in process listings and shell/agent
    # history, so credentials must never travel that way. Use stdin or a file.
    parser.add_argument("--json-file", dest="json_file",
                        help="path to a setup-spec JSON file (use '-' for stdin)")
    parser.add_argument("--json-stdin", action="store_true",
                        help="read the setup spec from stdin (preferred for secrets)")
    args = parser.parse_args(argv)

    if args.non_interactive:
        _setup_non_interactive(_load_spec(args.json_file, args.json_stdin))
        return 0

    print(
        "=== sql-buddy setup ===\n"
        f"Configures {CONFIG_FILE}"
    )
    mode = _choose_mode()
    if mode == 1:
        _setup_auto()
    else:
        _setup_external()
    return 0


# --- non-interactive spec -> db.ini ---------------------------------------
# Driven by a JSON object so an agent (or CI) can produce db.ini without the
# interactive prompts. Secrets stay local: they go straight from the spec into
# db.ini, never echoed.
#
# Schema (auto mode):
#   {"mode":"auto",
#    "ssh": {"name":"prod","host":"...","port":22,"user":"...","key":"~/.ssh/...",
#            "server_alive_interval":30,"server_alive_count_max":3},
#    "connections":[{"name":"ck","type":"clickhouse",
#                    "remote_host":"10.0.0.21","remote_port":9000,"local_port":10021,
#                    "user":"ro","password":"...","database":"default"}]}
#
# Schema (external mode):
#   {"mode":"external",
#    "connections":[{"name":"ck","type":"clickhouse","host":"localhost","port":9000,
#                    "user":"ro","password":"...","database":"default"}]}

def _guess_type(name: str) -> str:
    """Mirror the section-name inference used at query time (ck*->clickhouse, else mysql)."""
    low = name.lower()
    if low.startswith("ck"):
        return "clickhouse"
    if low.startswith(("db", "mysql")):
        return "mysql"
    return "mysql"


_VALID_TYPES = frozenset({"clickhouse", "mysql", "mariadb"})


def _require(d: dict, key: str, where: str, *, allow_empty: bool = False):
    """Require a field. `allow_empty=True` permits '' (passwordless DB users)."""
    if key not in d or d[key] is None:
        raise SystemExit(f"{where}: missing required field '{key}'")
    if d[key] == "" and not allow_empty:
        raise SystemExit(f"{where}: missing required field '{key}'")
    return d[key]


def _require_port(d: dict, key: str, where: str, default=None) -> int:
    """Require an integer port in 1..65535 (matches runtime int(port))."""
    if key not in d or d[key] in (None, ""):
        if default is not None:
            return int(default)
        raise SystemExit(f"{where}: missing required field '{key}'")
    try:
        p = int(d[key])
    except (TypeError, ValueError):
        raise SystemExit(f"{where}: '{key}' must be an integer port, got {d[key]!r}")
    if not (1 <= p <= 65535):
        raise SystemExit(f"{where}: '{key}' must be 1..65535, got {p}")
    return p


def _resolve_type(conn: dict, name: str) -> str:
    raw = conn.get("type")
    if raw in (None, ""):
        return _guess_type(name)
    ctype = str(raw).strip().lower()
    if ctype not in _VALID_TYPES:
        raise SystemExit(
            f"connection [{name}]: unsupported type '{ctype}' "
            f"(want clickhouse|mysql|mariadb)"
        )
    return ctype


def _add_conn_section(cfg, conn: dict, *, auto_block: str | None):
    name = _require(conn, "name", "connection")
    # Runtime skips every [ssh:*] section as a bastion block (case-insensitive).
    if str(name).lower().startswith("ssh:"):
        raise SystemExit(
            f"connection name '{name}' is reserved (ssh:* sections are bastions, "
            "not Connections)"
        )
    if cfg.has_section(name) or cfg.has_section(f"ssh:{name}"):
        raise SystemExit(f"connection name '{name}' is duplicated or reserved")
    cfg.add_section(name)
    cfg.set(name, "type", _resolve_type(conn, name))
    if auto_block is not None:
        cfg.set(name, "tunnel", f"ssh:{auto_block}")
        cfg.set(name, "remote_host",
                str(_require(conn, "remote_host", f"connection [{name}]")))
        cfg.set(name, "remote_port",
                str(_require_port(conn, "remote_port", f"connection [{name}]")))
        cfg.set(name, "local_port",
                str(_require_port(conn, "local_port", f"connection [{name}]")))
        cfg.set(name, "user", str(_require(conn, "user", f"connection [{name}]")))
        # Empty password is valid (passwordless local MySQL / some ClickHouse setups).
        cfg.set(name, "password",
                str(_require(conn, "password", f"connection [{name}]",
                             allow_empty=True)))
    else:
        cfg.set(name, "host", str(_require(conn, "host", f"connection [{name}]")))
        cfg.set(name, "port",
                str(_require_port(conn, "port", f"connection [{name}]")))
        cfg.set(name, "user", str(_require(conn, "user", f"connection [{name}]")))
        cfg.set(name, "password",
                str(_require(conn, "password", f"connection [{name}]",
                             allow_empty=True)))
    if conn.get("database"):
        cfg.set(name, "database", str(conn["database"]))


def _cfg_from_auto_spec(spec: dict) -> tuple[configparser.RawConfigParser, str]:
    ssh = spec.get("ssh") or {}
    block = ssh.get("name") or "prod"
    section = f"ssh:{block}"
    cfg = configparser.RawConfigParser()
    cfg.add_section(section)
    cfg.set(section, "host", str(_require(ssh, "host", "ssh")))
    cfg.set(section, "port", str(_require_port(ssh, "port", "ssh", default=22)))
    cfg.set(section, "user", str(_require(ssh, "user", "ssh")))
    key = str(_require(ssh, "key", "ssh")).strip().strip('"')
    cfg.set(section, "key", os.path.normpath(os.path.expanduser(key)))
    # keepalive is seconds (not a TCP port) — any non-negative / positive int.
    if "server_alive_interval" in ssh:
        try:
            sai = int(ssh["server_alive_interval"])
            if sai < 0:
                raise ValueError
        except (TypeError, ValueError):
            raise SystemExit("ssh: server_alive_interval must be a non-negative integer")
        cfg.set(section, "server_alive_interval", str(sai))
    else:
        cfg.set(section, "server_alive_interval", "30")
    if "server_alive_count_max" in ssh:
        try:
            sac = int(ssh["server_alive_count_max"])
            if sac < 1:
                raise ValueError
        except (TypeError, ValueError):
            raise SystemExit("ssh: server_alive_count_max must be a positive integer")
        cfg.set(section, "server_alive_count_max", str(sac))
    else:
        cfg.set(section, "server_alive_count_max", "3")
    conns = spec.get("connections") or []
    if not conns:
        raise SystemExit("at least one connection is required")
    for c in conns:
        _add_conn_section(cfg, c, auto_block=block)
    return cfg, "auto - self-managed SSH tunnels"


def _cfg_from_external_spec(spec: dict) -> tuple[configparser.RawConfigParser, str]:
    cfg = configparser.RawConfigParser()
    conns = spec.get("connections") or []
    if not conns:
        raise SystemExit("at least one connection is required")
    for c in conns:
        _add_conn_section(cfg, c, auto_block=None)
    return cfg, "external"


def _load_spec(json_file: str | None, json_stdin: bool) -> dict:
    """Resolve the setup spec to a JSON object.

    Secret-safe sources only (no inline argv JSON):
      stdin (--json-stdin, or --json-file -) > protected file (--json-file)
      > $SQL_BUDDY_SETUP_JSON env.
    """
    raw: str | None = None
    if json_stdin or json_file == "-":
        raw = sys.stdin.read()
    elif json_file:
        with open(os.path.expanduser(json_file)) as f:
            raw = f.read()
    elif os.environ.get("SQL_BUDDY_SETUP_JSON") is not None:
        raw = os.environ.get("SQL_BUDDY_SETUP_JSON")
    if raw is None:
        raise SystemExit(
            "--non-interactive needs one of: --json-stdin / --json-file <path|-> "
            "/ $SQL_BUDDY_SETUP_JSON (credentials must not travel via argv)"
        )
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"invalid setup JSON: {e}")
    if not isinstance(spec, dict):
        raise SystemExit("setup spec must be a JSON object")
    return spec


def _setup_non_interactive(spec: dict):
    mode = (spec.get("mode") or "auto").lower()
    if mode == "auto":
        cfg, label = _cfg_from_auto_spec(spec)
    elif mode == "external":
        cfg, label = _cfg_from_external_spec(spec)
    else:
        raise SystemExit(f"unknown mode '{mode}' (want 'auto' or 'external')")
    _write_cfg(cfg, label)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted - db.ini unchanged.")
        sys.exit(1)
