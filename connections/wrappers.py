"""Connection wrappers: raw and read-only-guarded DB clients.

`MySQLConnectionWrapper` adapts a raw pymysql connection to the dataframe/
execute interface the rest of sql-buddy expects. The `Locked*` variants wrap a
client and route every statement through the read-only guard (see `readonly`)
so write keywords can never reach the network.

See UBIQUITOUS_LANGUAGE.md: "Locked" == the wrapper layer enforcing Read-only.
"""

import pymysql
from clickhouse_driver import Client

from .readonly import _filter_sql


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

    def query_rows(self, sql: str):
        """Read `sql` and return (column_names, rows_as_dicts).

        Replaces the old `query_dataframe` (which wrapped these same rows in a
        pandas DataFrame — a 45 MiB import per process). Same DictCursor
        semantics, plain dicts out.
        """
        cursor = self._conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return columns, list(rows)
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

    def query_rows(self, sql: str):
        _filter_sql(sql)
        return self._wrapped.query_rows(sql)


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

    def disconnect(self):
        return self._wrapped.disconnect()

    @property
    def connected(self):
        return self._wrapped.connected
