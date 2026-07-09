"""Connection management for sql-buddy.

Public surface::

    from connections import DBManager, LockedDBManager, connection_type

    db = LockedDBManager()      # read-only guarded clients (the default)
    ck = db.ck                  # a Locked ClickHouse client
    mysql = db.mysql            # a Locked MySQL client

`LockedDBManager` is what the MCP server uses: every client it hands out is
wrapped by the read-only guard, so write keywords can never reach the network.
`DBManager` returns raw clients (used by tooling that supplies its own guard).

Implementation lives in focused submodules:
    readonly  - the SQL read-only filter
    runtime   - WSL / container / Windows-host-IP detection
    wrappers  - raw + Locked client wrappers
    manager   - DBManager / LockedDBManager + connection_type
"""

from .manager import DBManager, LockedDBManager, connection_type
from .runtime import is_container, is_wsl

__all__ = [
    'DBManager',
    'LockedDBManager',
    'connection_type',
    'is_wsl',
    'is_container',
]
