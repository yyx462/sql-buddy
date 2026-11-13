"""DBManager: builds and caches DB clients from db.ini sections.

`DBManager` returns raw driver clients; `LockedDBManager` returns clients
wrapped by the read-only guard (the "Locked" layer). Both honor the
self-managed SSH tunnel (`tunnel.py`): a tunneled connection connects to
`localhost:local_port`, and the cached client is rebuilt whenever the tunnel's
generation bumps (the previous TCP socket is dead after a restart).
"""

import configparser
import os
import sys
from contextlib import contextmanager
from typing import Any, Dict, Tuple

import pymysql
from clickhouse_driver import Client

from .config_util import read_config, resolve_env, config_path
from .runtime import get_windows_host_ip, is_wsl
from .wrappers import (
    LockedClickHouseClient,
    LockedMySQLConnectionWrapper,
    MySQLConnectionWrapper,
)

# tunnel.py lives at the repo root; make sure it's importable regardless of cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _get_tunnel_manager():
    """Return the process-wide TunnelManager (imported lazily).

    A module-level `from tunnel import get_manager` here creates a genuine
    import cycle: `import tunnel` -> `connections.config_util` ->
    `connections/__init__` -> manager -> `from tunnel import get_manager`
    while tunnel is still mid-initialization. tunnel.py's `except ImportError`
    then silently swallows the collision and its config helpers stay None,
    breaking every TunnelManager built in that process. Deferring the import
    to call time breaks the cycle, so `import tunnel` works in any order.
    """
    from tunnel import get_manager
    return get_manager()


DEFAULT_REMOTE_DB_CREDS: Dict[str, Tuple[str, str]] = {}


def connection_type(cfg, section: str) -> str:
    """Resolve a connection's DBMS type.

    Reads an explicit `type = clickhouse|mysql` from the section; falls back to
    inferring from the section-name prefix so older configs keep working.
    """
    explicit = cfg.get(section, 'type', fallback=None)
    if explicit:
        return explicit.strip().lower()
    if section.startswith("ck"):
        return 'clickhouse'
    if section.startswith("db") or section.startswith("mysql"):
        return 'mysql'
    raise ValueError(
        f"Unknown connection type for [{section}]; set `type = clickhouse|mysql` in db.ini"
    )


class DBManager:
    """Unlocked DBManager - returns raw connections."""

    def __init__(self, config_file: str = None, remote_db_creds: Dict[str, Tuple[str, str]] = None, wsl_host_override: bool = True):
        if config_file is None:
            config_file = config_path()
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Config file not found: {config_file}")

        cfg = read_config(config_file)
        self.config_file = config_file
        self._cfg = cfg
        self._cache = {}
        self._remote_db_creds = remote_db_creds or DEFAULT_REMOTE_DB_CREDS
        self._wsl_host_override = wsl_host_override

    @classmethod
    def get(cls, name: str, config_file: str = None, **kwargs):
        return cls(config_file, **kwargs).getattr(name)

    @staticmethod
    def load_config(config_file: str) -> configparser.RawConfigParser:
        if config_file is None:
            config_file = config_path()
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Config file not found: {config_file}")
        return read_config(config_file)

    def __getattr__(self, name: str):
        section = name.replace("_", "-")
        if section not in self._cfg:
            raise AttributeError(f"No section [{section}] in config")

        tm = _get_tunnel_manager()
        block = tm.block_for(section)
        if block is not None:
            # Bring up (or confirm) the self-managed SSH tunnel before connecting.
            tm.ensure_for(section)
        # Generation changes when the tunnel was (re)started -> the cached client's
        # TCP socket is dead and must be rebuilt. 0 for non-tunneled connections.
        gen = tm.generation(block) if block is not None else 0

        cached = self._cache.get(name)
        if cached is not None and cached['gen'] == gen:
            return cached['client']

        ctype = connection_type(self._cfg, section)
        if ctype == 'clickhouse':
            client = self._create_clickhouse_client(section)
        elif ctype in ('mysql', 'mariadb'):
            client = self._create_mysql_client(section)
        else:
            raise ValueError(f"Unsupported connection type '{ctype}' for [{section}]")

        self._cache[name] = {'client': client, 'gen': gen}
        return client

    def _resolve_conn_config(self, section: str, default_port: int) -> dict:
        """Build driver kwargs for a section: strip meta keys, resolve host/port.

        Self-tunneled connections connect to localhost:local_port (the SSH forward).
        Otherwise the legacy WSL host-override applies when running inside WSL.
        """
        conn_config = dict(self._cfg[section])
        # Meta keys consumed elsewhere, not by the DB driver.
        for meta in ('type', 'tunnel', 'remote_host', 'remote_port', 'local_port'):
            conn_config.pop(meta, None)
        for k, v in list(conn_config.items()):
            conn_config[k] = resolve_env(str(v))

        if _get_tunnel_manager().block_for(section) is not None:
            conn_config['host'] = 'localhost'
            conn_config['port'] = int(_get_tunnel_manager().local_port_for(section))
        else:
            if self._wsl_host_override and is_wsl():
                windows_ip = get_windows_host_ip()
                if windows_ip:
                    conn_config['host'] = windows_ip
            conn_config['port'] = int(conn_config.get('port', default_port))
        return conn_config

    def _create_clickhouse_client(self, section: str) -> Client:
        cfg = self._resolve_conn_config(section, 9000)
        # Default timeouts: a black-holed endpoint (dead tunnel forward, silent
        # DB) must raise instead of parking the caller's worker thread in a
        # socket read forever. Explicit keys in the db.ini section win.
        cfg.setdefault('connect_timeout', 10)
        cfg.setdefault('send_receive_timeout', 900)
        for k in ('connect_timeout', 'send_receive_timeout'):
            cfg[k] = int(cfg[k])  # ini values arrive as strings; drivers want ints
        return Client(**cfg)

    def _create_mysql_client(self, section: str) -> MySQLConnectionWrapper:
        cfg = self._resolve_conn_config(section, 3306)
        # Same bounds for MySQL; read/write must exceed the 600s
        # innodb_lock_wait_timeout the locked wrapper sets, so legit
        # lock-waits are not cut short.
        cfg.setdefault('connect_timeout', 10)
        cfg.setdefault('read_timeout', 900)
        cfg.setdefault('write_timeout', 900)
        for k in ('connect_timeout', 'read_timeout', 'write_timeout'):
            cfg[k] = int(cfg[k])  # ini values arrive as strings; drivers want ints
        return MySQLConnectionWrapper(pymysql.connect(**cfg))

    def all_clients(self) -> Dict[str, Any]:
        return {name: entry['client'] for name, entry in self._cache.items()}

    def close_all(self):
        for entry in self._cache.values():
            client = entry['client']
            if hasattr(client, "disconnect"):
                client.disconnect()
            elif hasattr(client, "close"):
                client.close()

    @contextmanager
    def transaction_scope(self, connection: str):
        conn = getattr(self, connection)
        cursor = conn.cursor()
        cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        cursor.execute("SET SESSION innodb_lock_wait_timeout = 600")
        cursor.close()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


class LockedDBManager(DBManager):
    """Returns read-only wrapped connections."""

    def _create_clickhouse_client(self, section: str) -> LockedClickHouseClient:
        return LockedClickHouseClient(super()._create_clickhouse_client(section))

    def _create_mysql_client(self, section: str) -> LockedMySQLConnectionWrapper:
        return LockedMySQLConnectionWrapper(super()._create_mysql_client(section))
