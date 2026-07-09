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
    name = _ask("bastion name", "prod")
    section = f"ssh:{name}"
    cfg.add_section(section)
    cfg.set(section, "host", _ask("SSH host"))
    cfg.set(section, "port", str(_ask_int("SSH port", 22)))
    cfg.set(section, "user", _ask("SSH user"))
    cfg.set(section, "key", _ask("Private key path (e.g. ~/.ssh/id_ed25519)"))
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
    if not os.path.exists(CONFIG_FILE):
        return None
    n = 1
    while os.path.exists(f"{CONFIG_FILE}.bak{n}"):
        n += 1
    bak = f"{CONFIG_FILE}.bak{n}"
    shutil.copy2(CONFIG_FILE, bak)
    return bak


def _setup_auto():
    cfg = configparser.RawConfigParser()
    block = _ask_bastion(cfg)
    _add_connection_auto(cfg, block)
    while _yes_no("\nAdd another connection?", default=False):
        _add_connection_auto(cfg, block)

    bak = _backup_existing()
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        cfg.write(f)
    print(f"\nWrote {CONFIG_FILE}  (mode: auto - self-managed SSH tunnels)")
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


def main():
    print(
        "=== sql-buddy setup ===\n"
        f"Configures {CONFIG_FILE}"
    )
    mode = _choose_mode()
    if mode == 1:
        _setup_auto()
    else:
        _setup_external()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted - db.ini unchanged.")
        sys.exit(1)
