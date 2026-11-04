"""Writer/reader contract: _create_* injects default timeouts, db.ini wins.

Receipt (2026-11-04): a sql-buddy MCP process pinned the mini at ~950% CPU
with 83 worker threads parked forever in socket reads — pymysql/clickhouse
clients were built with no connect/read timeouts, so a black-holed tunnel
endpoint parked every calling thread permanently. These tests pin the fix:
defaults are injected, explicit section keys override them.
"""

import configparser
import threading

import pytest

from connections.manager import DBManager


class _Capture:
    """Records the kwargs it was constructed with; never touches a network."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def manager(tmp_path, monkeypatch):
    ini = tmp_path / "db.ini"
    ini.write_text(
        "[ck-default]\n"
        "type = clickhouse\n"
        "host = db.example\n"
        "\n"
        "[ck-explicit]\n"
        "type = clickhouse\n"
        "host = db.example\n"
        "connect_timeout = 3\n"
        "send_receive_timeout = 60\n"
        "\n"
        "[mysql-default]\n"
        "type = mysql\n"
        "host = db.example\n"
        "\n"
        "[mysql-explicit]\n"
        "type = mysql\n"
        "host = db.example\n"
        "connect_timeout = 2\n"
        "read_timeout = 30\n"
    )

    # No tunnels in these sections -> TunnelManager stays idle.
    import connections.manager as m
    monkeypatch.setattr(m, "_get_tunnel_manager",
                        lambda: type("T", (), {"block_for": staticmethod(lambda s: None),
                                               "local_port_for": staticmethod(lambda s: None),
                                               "generation": staticmethod(lambda b: 0)})())
    return DBManager(str(ini))


def test_clickhouse_defaults_injected(manager, monkeypatch):
    seen = {}
    monkeypatch.setattr("connections.manager.Client",
                        lambda **kw: seen.update(kw) or _Capture(**kw))
    manager._create_clickhouse_client("ck-default")
    assert seen["connect_timeout"] == 10
    assert seen["send_receive_timeout"] == 900


def test_clickhouse_section_overrides(manager, monkeypatch):
    seen = {}
    monkeypatch.setattr("connections.manager.Client",
                        lambda **kw: seen.update(kw) or _Capture(**kw))
    manager._create_clickhouse_client("ck-explicit")
    assert seen["connect_timeout"] == 3
    assert seen["send_receive_timeout"] == 60


def test_mysql_defaults_injected(manager, monkeypatch):
    seen = {}

    class _Conn(_Capture):
        pass

    monkeypatch.setattr("connections.manager.pymysql",
                        type("P", (), {"connect": staticmethod(lambda **kw: seen.update(kw) or _Conn(**kw))}))
    manager._create_mysql_client("mysql-default")
    assert seen["connect_timeout"] == 10
    assert seen["read_timeout"] == 900
    assert seen["write_timeout"] == 900


def test_mysql_section_overrides(manager, monkeypatch):
    seen = {}

    class _Conn(_Capture):
        pass

    monkeypatch.setattr("connections.manager.pymysql",
                        type("P", (), {"connect": staticmethod(lambda **kw: seen.update(kw) or _Conn(**kw))}))
    manager._create_mysql_client("mysql-explicit")
    assert seen["connect_timeout"] == 2
    assert seen["read_timeout"] == 30
    # Not set in the section -> default still injected alongside overrides.
    assert seen["write_timeout"] == 900
