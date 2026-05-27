"""
Locked Database Connection Manager - Read-Only Filter

Usage:
    from db_conn_locked import LockedDBManager

    db = LockedDBManager()
    ck = db.ck           # Read-only ClickHouse
    mysql = db.db        # Read-only MySQL
"""

import configparser
from contextlib import contextmanager
import os
import re
import uuid
import pymysql
from clickhouse_driver import Client
import pandas as pd
from typing import Optional, Dict, Tuple, Any


# Read-Only Filter
WRITE_KEYWORDS = [
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER',
    'TRUNCATE', 'REPLACE', 'MERGE', 'CALL', 'EXEC', 'EXECUTE',
    'GRANT', 'REVOKE', 'LOCK', 'UNLOCK', 'SET GLOBAL',
    'KILL', 'LOAD DATA', 'START TRANSACTION', 'BEGIN'
]

READ_ONLY_KEYWORDS = [
    'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN',
    'USE', 'WITH', 'EXISTS', 'CHECK', 'HELP'
]


def _is_write_operation(sql: str) -> bool:
    sql_upper = sql.strip().upper()
    for keyword in READ_ONLY_KEYWORDS:
        if sql_upper.startswith(keyword):
            return False
    for keyword in WRITE_KEYWORDS:
        pattern = r'\b' + keyword + r'\b'
        if re.search(pattern, sql_upper):
            if keyword == 'CREATE' and 'SHOW' in sql_upper:
                continue
            return True
    return False


def _filter_sql(sql: str) -> str:
    if _is_write_operation(sql):
        raise PermissionError(
            f"Write operation blocked by db_conn_locked. "
            f"Only READ queries are allowed. Blocked SQL: {sql[:100]}..."
        )
    return sql


def is_wsl() -> bool:
    if is_container():
        return False
    try:
        with open('/proc/version', 'r') as f:
            version = f.read().lower()
            return 'microsoft' in version or 'wsl' in version
    except (FileNotFoundError, IOError):
        return False


def is_container() -> bool:
    if os.path.exists('/.dockerenv'):
        return True
    try:
        with open('/proc/1/cgroup', 'r') as f:
            cgroup = f.read()
            if any(sig in cgroup for sig in ['docker', 'containerd', 'kubepods', 'lxc']):
                return True
    except (FileNotFoundError, IOError):
        pass
    for var in ['DOCKER_CONTAINER', 'DOCKER_ID', 'CONTAINER', 'KUBERNETES_SERVICE_HOST']:
        if os.environ.get(var):
            return True
    return False


def get_windows_host_ip() -> Optional[str]:
    if not is_wsl():
        return None
    try:
        with open('/etc/resolv.conf', 'r') as f:
            for line in f:
                if line.strip().startswith('nameserver'):
                    ip = line.split()[1]
                    if ip and ip != '127.0.0.1':
                        return ip
    except (FileNotFoundError, IOError):
        pass
    return None


DEFAULT_REMOTE_DB_CREDS: Dict[str, Tuple[str, str]] = {}


class MySQLConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def db_execute(self, sql: str, params: tuple = None) -> int:
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            self._conn.commit()
            return cursor.rowcount
        finally:
            cursor.close()

    def query_dataframe(self, sql: str) -> pd.DataFrame:
        cursor = self._conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute(sql)
            rows = cursor.fetchall()
            return pd.DataFrame(rows)
        finally:
            cursor.close()


class LockedMySQLConnectionWrapper:
    def __init__(self, wrapped: MySQLConnectionWrapper):
        self._wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def cursor(self, *args, **kwargs):
        return self._wrapped.cursor(*args, **kwargs)

    def commit(self):
        return self._wrapped.commit()

    def rollback(self):
        return self._wrapped.rollback()

    def close(self):
        return self._wrapped.close()

    @property
    def open(self):
        return self._wrapped.open

    def db_execute(self, sql: str, params: tuple = None) -> int:
        _filter_sql(sql)
        return self._wrapped.db_execute(sql, params)

    def query_dataframe(self, sql: str) -> pd.DataFrame:
        _filter_sql(sql)
        return self._wrapped.query_dataframe(sql)


class LockedClickHouseClient:
    def __init__(self, wrapped: Client):
        self._wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def execute(self, query, params=None, with_column_types=False, settings=None, **kwargs):
        _filter_sql(query)
        return self._wrapped.execute(
            query, params=params,
            with_column_types=with_column_types,
            settings=settings, **kwargs
        )

    def query_dataframe(self, query, params=None, settings=None, **kwargs):
        _filter_sql(query)
        result = self._wrapped.execute(query, params=params, settings=settings, with_column_types=True, **kwargs)
        if result:
            data, columns = result
            if data:
                return pd.DataFrame(data, columns=[c[0] for c in columns])
        return pd.DataFrame()

    def disconnect(self):
        return self._wrapped.disconnect()

    @property
    def connected(self):
        return self._wrapped.connected


class DBManager:
    """Unlocked DBManager - returns raw connections."""

    def __init__(self, config_file: str = None, remote_db_creds: Dict[str, Tuple[str, str]] = None, wsl_host_override: bool = True):
        if config_file is None:
            config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'db.ini')
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Config file not found: {config_file}")

        cfg = configparser.RawConfigParser()
        cfg.read(config_file)
        self.config_file = config_file
        self._cfg = cfg
        self._cache = {}
        self._remote_db_creds = remote_db_creds or DEFAULT_REMOTE_DB_CREDS
        self._wsl_host_override = wsl_host_override

    @classmethod
    def get(cls, name: str, config_file: str = None, **kwargs):
        return cls(config_file, **kwargs).getattr(name)

    def __getattr__(self, name: str):
        section = name.replace("_", "-")
        if section not in self._cfg:
            raise AttributeError(f"No section [{section}] in config")
        if name in self._cache:
            return self._cache[name]

        if section.startswith("ck"):
            client = self._create_clickhouse_client(section)
        elif section.startswith("db") or section.startswith("mysql"):
            client = self._create_mysql_client(section)
        else:
            raise ValueError(f"Unknown client type: {section}")

        self._cache[name] = client
        return client

    def _create_clickhouse_client(self, section: str) -> Client:
        conn_config = dict(self._cfg[section])
        if self._wsl_host_override and is_wsl():
            windows_ip = get_windows_host_ip()
            if windows_ip:
                conn_config['host'] = windows_ip
        conn_config['port'] = int(conn_config.get('port', 9000))
        return Client(**conn_config)

    def _create_mysql_client(self, section: str) -> MySQLConnectionWrapper:
        conn_config = dict(self._cfg[section])
        if self._wsl_host_override and is_wsl():
            windows_ip = get_windows_host_ip()
            if windows_ip:
                conn_config['host'] = windows_ip
        conn_config['port'] = int(conn_config.get('port', 3306))
        conn = pymysql.connect(**conn_config)
        return MySQLConnectionWrapper(conn)

    def all_clients(self) -> Dict[str, Any]:
        return self._cache

    def close_all(self):
        for name, client in self._cache.items():
            if hasattr(client, "disconnect"):
                client.disconnect()
            elif hasattr(client, "close"):
                client.close()

    @contextmanager
    def session_scope(self, client_name: str = 'ck'):
        client = getattr(self, client_name)
        session_id = str(uuid.uuid4())
        original_execute = client.execute
        original_query_dataframe = getattr(client, 'query_dataframe', None)

        def scoped_execute(query, params=None, settings=None, **kwargs):
            if settings is None:
                settings = {}
            settings['session_id'] = session_id
            return original_execute(query, params=params, settings=settings, **kwargs)

        def scoped_query_dataframe(query, params=None, settings=None, **kwargs):
            if settings is None:
                settings = {}
            settings['session_id'] = session_id
            return original_query_dataframe(query, params=params, settings=settings, **kwargs)

        class SessionClient:
            def execute(self, *args, **kwargs):
                return scoped_execute(*args, **kwargs)
            def query_dataframe(self, *args, **kwargs):
                if original_query_dataframe:
                    return scoped_query_dataframe(*args, **kwargs)
                raise AttributeError("query_dataframe not available on this client")

        yield SessionClient()

    @contextmanager
    def transaction_scope(self, db_name: str):
        conn = getattr(self, db_name)
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


__all__ = ['DBManager', 'LockedDBManager', 'get_remote_db', 'is_wsl', 'is_container']
