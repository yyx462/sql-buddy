"""Contract tests for tunnel.py + the tunneled paths of connections/manager.py.

tunnel.py owns real `ssh -N` subprocesses, so these tests exercise the FULL
lifecycle — spawn, port-forward readiness, per-bastion singleton reuse,
restart-on-death, stderr tail bounding, teardown — against a *fake ssh*
executable that mimics the parts of OpenSSH sql-buddy depends on: it parses
`-L 127.0.0.1:lp:rh:rp` forwards, actually binds those local ports, and can be
told to die or start slowly (modes via $FAKE_SSH_MODE). No bastion, no network
beyond localhost, no secrets.

Pinned contracts:

  * config parsing  — `[ssh:*]` blocks + per-connection `tunnel` mappings,
    with `_`/`-` name interchangeability and clear errors on misconfig.
  * command shape   — `-N`, ExitOnForwardFailure=yes,
    StrictHostKeyChecking=accept-new, localhost-only `-L` binds, expanded
    `~`/`$env:` values in `-i`/user/host/port.
  * lifecycle       — one process per `[ssh:*]` block even with several
    connections behind it; generation bump + client rebuild on restart.
  * manager glue    — tunneled sections connect to `localhost:local_port`;
    meta keys never reach the driver; Locked layer still wraps.

Run: `python -m pytest tests/test_tunnel.py` from the repo root (or standalone:
`python3 tests/test_tunnel.py`).
"""

import os
import socket
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tunnel  # noqa: E402
from connections.manager import DBManager, LockedDBManager  # noqa: E402


# --- helpers ---------------------------------------------------------------

FAKE_SSH = r'''#!/usr/bin/env python3
"""Mimic the slice of OpenSSH sql-buddy relies on: bind -L forwards, stay up."""
import os, socket, sys, time

mode = os.environ.get("FAKE_SSH_MODE", "ok")

if mode == "die":
    sys.stderr.write("fake-ssh: Connection refused by bastion (pretend)\n")
    sys.stderr.write("fake-ssh: exiting 255\n")
    sys.stderr.flush()
    time.sleep(0.3)
    sys.exit(255)

ports = []
args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "-L":
        # -L 127.0.0.1:<local>:<remote_host>:<remote_port>
        ports.append(int(a and args[i + 1].split(":")[1]))

if mode == "slow":
    time.sleep(0.5)

if mode == "spam":
    for n in range(1, 61):
        sys.stderr.write("spam line %d\n" % n)
    sys.stderr.flush()

socks = []
for p in ports:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", p))
    s.listen(5)
    socks.append(s)

sys.stderr.write("fake-ssh: listening\n")
sys.stderr.flush()
while True:
    time.sleep(3600)
'''


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    """Install the fake ssh binary and make tunnel.py resolve to it."""
    if sys.platform == "win32":
        pytest.skip("fake ssh is a shebang script; lifecycle tests run on posix")
    path = tmp_path / "fake-ssh"
    path.write_text(FAKE_SSH)
    path.chmod(0o755)
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: str(path))
    return path


@pytest.fixture
def manager(tmp_path, fake_ssh):
    """A TunnelManager over a config with one bastion and two connections behind it."""
    ini = tmp_path / "db.ini"
    lp1, lp2 = _free_port(), _free_port()
    ini.write_text(
        "[ssh:bastion]\n"
        "host = 127.0.0.1\n"
        "port = 2222\n"
        "user = tester\n"
        "key = ~/.ssh/id_test\n"
        "\n"
        "[ck-one]\n"
        "type = clickhouse\n"
        "tunnel = ssh:bastion\n"
        f"local_port = {lp1}\n"
        "remote_host = 10.0.0.1\n"
        "remote_port = 9000\n"
        "\n"
        "[ck-two]\n"
        "type = clickhouse\n"
        "tunnel = bastion\n"          # bare block name must work too
        f"local_port = {lp2}\n"
        "remote_host = 10.0.0.2\n"
        "remote_port = 9000\n"
    )
    return tunnel.TunnelManager(config_file=str(ini))


def _wait_until(fn, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.05)
    return False


# --- config parsing (pure logic) --------------------------------------------

def test_normalize_interchanges_underscore_and_dash():
    assert tunnel._normalize("ck_prod") == "ck-prod"
    assert tunnel._normalize("ck-prod") == "ck-prod"


def test_ssh_blocks_collect_sections_and_strip_prefix(tmp_path):
    ini = tmp_path / "db.ini"
    ini.write_text("[ssh:prod]\nhost = h\n\n[SSH:UPPER]\nhost = h2\n\n[ck]\ntype = mysql\n")
    blocks = tunnel._ssh_blocks(tunnel._read_config(str(ini)))
    assert set(blocks) == {"prod", "UPPER"}
    assert blocks["prod"]["host"] == "h"


def test_conn_tunnels_parses_and_strips_ssh_prefix(tmp_path):
    ini = tmp_path / "db.ini"
    ini.write_text(
        "[ssh:bastion]\nhost = h\n"
        "[a]\ntunnel = ssh:bastion\nlocal_port = 1\nremote_host = rh\nremote_port = 2\n"
        "[b]\ntunnel = bastion\nlocal_port = 3\nremote_host = rh2\nremote_port = 4\n"
    )
    out = tunnel._conn_tunnels(tunnel._read_config(str(ini)))
    assert out["a"] == ("bastion", 1, "rh", 2)
    assert out["b"] == ("bastion", 3, "rh2", 4)


def test_conn_tunnels_rejects_missing_or_bad_ports(tmp_path):
    ini = tmp_path / "db.ini"
    ini.write_text(
        "[ssh:b]\nhost = h\n"
        "[bad-ports]\ntunnel = b\nlocal_port = x\nremote_port = y\nremote_host = rh\n"
        "[no-remote-host]\ntunnel = b\nlocal_port = 1\nremote_port = 2\n"
    )
    cfg = tunnel._read_config(str(ini))
    with pytest.raises(ValueError, match="integer"):
        tunnel._conn_tunnels(cfg)
    cfg.remove_section("bad-ports")
    with pytest.raises(ValueError, match="remote_host"):
        tunnel._conn_tunnels(cfg)


def test_tunnel_introspection_and_name_normalization(manager):
    assert manager.block_for("ck_one") == "bastion"       # _ vs - interchange
    assert manager.block_for("ck-two") == "bastion"
    assert manager.block_for("db-plain") is None
    lp1 = manager.local_port_for("ck-one")
    lp2 = manager.local_port_for("ck-two")
    assert lp1 != lp2                          # distinct forwards, same bastion
    assert manager.block_for(lp1 and "db-plain") is None  # non-tunneled -> None
    assert manager.generation("bastion") == 0             # never started


def test_build_cmd_shape(tmp_path, monkeypatch):
    ini = tmp_path / "db.ini"
    ini.write_text(
        "[ssh:b]\n"
        "host = bastion.example\n"
        "port = 2222\n"
        "user = tester\n"
        "key = ~/.ssh/id_test\n"
        "[c]\ntunnel = b\nlocal_port = 1000\nremote_host = 10.0.0.1\nremote_port = 9000\n"
    )
    m = tunnel.TunnelManager(config_file=str(ini))
    m._load()
    cmd = m._build_cmd("b", [(1000, "10.0.0.1", 9000)])
    assert cmd[1] == "-N"
    assert "ExitOnForwardFailure=yes" in cmd
    assert "StrictHostKeyChecking=accept-new" in cmd
    assert "ServerAliveInterval=30" in cmd and "ServerAliveCountMax=3" in cmd
    i = cmd.index("-p")
    assert cmd[i + 1] == "2222"
    i = cmd.index("-i")
    assert cmd[i + 1] == os.path.join(os.path.expanduser("~"), ".ssh", "id_test")  # ~ expanded
    assert cmd[-1] == "tester@bastion.example"
    # localhost-only bind address — never a wildcard
    assert "127.0.0.1:1000:10.0.0.1:9000" in cmd


def test_build_cmd_resolves_env_and_defaults(tmp_path, monkeypatch):
    ini = tmp_path / "db.ini"
    ini.write_text(
        "[ssh:b]\n"
        "host = $env:SB_TEST_HOST\n"
        "user = $env:SB_TEST_USER\n"
        "key = $env:SB_TEST_KEY\n"
        "[c]\ntunnel = b\nlocal_port = 1000\nremote_host = rh\nremote_port = 9000\n"
    )
    monkeypatch.setenv("SB_TEST_HOST", "h.example")
    monkeypatch.setenv("SB_TEST_USER", "u1")
    monkeypatch.setenv("SB_TEST_KEY", "/keys/k")
    m = tunnel.TunnelManager(config_file=str(ini))
    m._load()
    cmd = m._build_cmd("b", [(1000, "rh", 9000)])
    assert cmd[-1] == "u1@h.example"
    # _build_cmd normpaths the key (Windows mixed-slash safety) — expect the
    # platform-native form, not the raw posix literal.
    assert cmd[cmd.index("-i") + 1] == os.path.normpath("/keys/k")
    assert cmd[cmd.index("-p") + 1] == "22"  # DEFAULT_SSH_PORT


def test_build_cmd_missing_env_raises(tmp_path, monkeypatch):
    ini = tmp_path / "db.ini"
    ini.write_text(
        "[ssh:b]\nhost = $env:SB_MISSING_VAR\nuser = u\nkey = /k\n"
        "[c]\ntunnel = b\nlocal_port = 1\nremote_host = rh\nremote_port = 2\n"
    )
    monkeypatch.delenv("SB_MISSING_VAR", raising=False)
    m = tunnel.TunnelManager(config_file=str(ini))
    m._load()
    with pytest.raises(EnvironmentError, match="SB_MISSING_VAR"):
        m._build_cmd("b", [(1, "rh", 2)])


def test_popen_kwargs_detaches_on_posix():
    kw = tunnel.TunnelManager()._popen_kwargs()
    assert kw["stdin"] == subprocess.DEVNULL
    assert kw["stdout"] == subprocess.DEVNULL
    assert kw["stderr"] == subprocess.PIPE
    if sys.platform != "win32":
        assert kw["start_new_session"] is True


# --- port readiness ----------------------------------------------------------

def test_port_open_true_and_false():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    assert tunnel._port_open(port) is True
    s.close()
    assert tunnel._port_open(port) is False


def test_wait_ready_success_when_ports_open(tmp_path):
    if sys.platform == "win32":
        pytest.skip("spawns /bin/sleep; posix only")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        m = tunnel.TunnelManager(config_file=str(tmp_path / "none.ini"))
        assert m._wait_ready("b", proc, [port], timeout=3.0) is True
    finally:
        proc.kill()


def test_wait_ready_fails_fast_when_ssh_dies(tmp_path):
    if sys.platform == "win32":
        pytest.skip("spawns /bin/true; posix only")
    proc = subprocess.Popen(["true"])
    proc.wait()
    m = tunnel.TunnelManager(config_file=str(tmp_path / "none.ini"))
    t0 = time.monotonic()
    assert m._wait_ready("b", proc, [_free_port()], timeout=10.0) is False
    assert time.monotonic() - t0 < 5.0  # did not sit out the whole timeout


def test_wait_ready_times_out_when_port_never_opens(tmp_path):
    if sys.platform == "win32":
        pytest.skip("spawns /bin/sleep; posix only")
    proc = subprocess.Popen(["sleep", "30"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        m = tunnel.TunnelManager(config_file=str(tmp_path / "none.ini"))
        t0 = time.monotonic()
        assert m._wait_ready("b", proc, [_free_port()], timeout=0.5) is False
        assert 0.4 < time.monotonic() - t0 < 5.0
    finally:
        proc.kill()


# --- lifecycle against the fake ssh ------------------------------------------

def test_ensure_for_returns_none_for_unconfigured_connection(manager):
    assert manager.ensure_for("db-not-in-tunnels") is None
    assert manager._procs == {}


def test_ensure_unknown_block_raises(manager):
    with pytest.raises(ValueError, match="ssh:ghost"):
        manager._ensure_block("ghost")


def test_ensure_block_noop_without_forwards(tmp_path, fake_ssh):
    ini = tmp_path / "db.ini"
    ini.write_text("[ssh:idle]\nhost = h\nuser = u\nkey = /k\n")
    m = tunnel.TunnelManager(config_file=str(ini))
    m._ensure_block("idle")           # defined but nothing references it
    assert m._procs == {}


def test_ensure_starts_tunnel_and_opens_ports(manager):
    block = manager.ensure_for("ck-one")
    assert block == "bastion"
    assert manager.generation("bastion") == 1
    proc = manager._procs["bastion"]
    assert proc.poll() is None
    lp = manager.local_port_for("ck-one")
    assert _wait_until(lambda: tunnel._port_open(lp))
    assert _wait_until(lambda: "fake-ssh: listening" in manager._read_stderr("bastion"))
    manager.stop_all()


def test_single_process_per_bastion_across_connections(manager):
    manager.ensure_for("ck-one")
    p1 = manager._procs["bastion"]
    manager.ensure_for("ck-two")      # same bastion -> must reuse
    assert manager._procs["bastion"] is p1
    assert manager.generation("bastion") == 1
    manager.stop_all()


def test_restart_bumps_generation_and_new_process(manager):
    manager.ensure_for("ck-one")
    p1 = manager._procs["bastion"]
    p1.kill()
    p1.wait()
    manager.ensure_for("ck-two")      # dead proc -> restart
    p2 = manager._procs["bastion"]
    assert p2 is not p1
    assert p2.poll() is None
    assert manager.generation("bastion") == 2
    manager.stop_all()


def test_ensure_raises_with_stderr_tail_when_ssh_dies(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_MODE", "die")
    ini = tmp_path / "db.ini"
    ini.write_text(
        "[ssh:b]\nhost = h\nuser = u\nkey = /k\n"
        "[c]\ntunnel = b\nlocal_port = %d\nremote_host = rh\nremote_port = 9\n" % _free_port()
    )
    m = tunnel.TunnelManager(config_file=str(ini))
    with pytest.raises(RuntimeError, match="did not come up") as exc:
        m.ensure_for("c")
    assert "Connection refused" in str(exc.value)     # ssh stderr surfaced
    assert m._procs == {}                             # corpse cleaned up
    # The drain thread for this block must terminate once ssh is gone —
    # not busy-spin on EOF. (Other blocks' drains may legitimately still run.)
    assert _wait_until(lambda: not any(
        t.name == "ssh-drain:b" and t.is_alive() for t in threading.enumerate()
    ), timeout=2.0)


def test_stderr_tail_is_bounded(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_MODE", "spam")       # 60 lines, keeps running
    ini = tmp_path / "db.ini"
    lp = _free_port()
    ini.write_text(
        "[ssh:b]\nhost = h\nuser = u\nkey = /k\n"
        "[c]\ntunnel = b\nlocal_port = %d\nremote_host = rh\nremote_port = 9\n" % lp
    )
    m = tunnel.TunnelManager(config_file=str(ini))
    m.ensure_for("c")
    assert _wait_until(lambda: "spam line 60" in m._read_stderr("b"))
    tail = m._read_stderr("b").splitlines()
    assert len(tail) <= tunnel.STDERR_TAIL_LINES      # bounded, no pipe deadlock
    assert tail[-1] == "fake-ssh: listening"          # most recent line kept
    assert "spam line 60" in tail and "spam line 1" not in tail  # older lines rotated out
    m.stop_all()


def test_stop_all_terminates_every_process(manager):
    manager.ensure_for("ck-one")
    proc = manager._procs["bastion"]
    manager.stop_all()
    assert proc.poll() is not None
    assert "bastion" not in manager._procs
    # Ports released: forward no longer accepts connections.
    lp = manager.local_port_for("ck-one")
    assert _wait_until(lambda: not tunnel._port_open(lp))


def test_concurrent_ensure_spawns_only_one_process(manager):
    manager._build_cmd_orig = manager._build_cmd
    gate = threading.Event()

    def slow_cmd(*a, **k):
        gate.wait(2.0)          # hold the spawn window open for both threads
        return manager._build_cmd_orig(*a, **k)

    manager._build_cmd = slow_cmd
    results = []

    def worker():
        try:
            results.append(manager.ensure_for("ck-one"))
        except Exception as e:  # noqa: BLE001
            results.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    gate.set()
    for t in threads:
        t.join()
    assert results == ["bastion", "bastion"]
    assert manager.generation("bastion") == 1   # exactly one spawn
    assert len(manager._procs) == 1
    manager.stop_all()


def test_reload_picks_up_config_edits(tmp_path, fake_ssh):
    ini = tmp_path / "db.ini"
    ini.write_text("[ssh:b]\nhost = h\nuser = u\nkey = /k\n")
    m = tunnel.TunnelManager(config_file=str(ini))
    assert m.block_for("c") is None
    lp = _free_port()
    ini.write_text(
        "[ssh:b]\nhost = h\nuser = u\nkey = /k\n"
        "[c]\ntunnel = b\nlocal_port = %d\nremote_host = rh\nremote_port = 9\n" % lp
    )
    m.reload()
    assert m.block_for("c") == "b"
    assert m.local_port_for("c") == lp


def test_load_auto_detects_config_edits_without_reload(tmp_path, fake_ssh):
    """Regression: a long-running MCP server loaded db.ini once at startup.

    After the user migrated a section to `tunnel =` on disk, the stale
    singleton kept answering block_for() from the old mapping, so tunneled
    connections were silently routed to the raw host instead of
    localhost:local_port. _load() must re-read when the file mtime changes.
    """
    ini = tmp_path / "db.ini"
    ini.write_text("[ssh:b]\nhost = h\nuser = u\nkey = /k\n")
    m = tunnel.TunnelManager(config_file=str(ini))
    assert m.block_for("c") is None          # loaded, no tunnel yet
    lp = _free_port()
    future = time.time() + 10                # guarantee an mtime change
    os.utime(ini, (future, future))
    ini.write_text(
        "[ssh:b]\nhost = h\nuser = u\nkey = /k\n"
        "[c]\ntunnel = b\nlocal_port = %d\nremote_host = rh\nremote_port = 9\n" % lp
    )
    os.utime(ini, (future, future))
    assert m.block_for("c") == "b"           # auto-detected, no reload() call
    assert m.local_port_for("c") == lp


# --- DBManager glue: tunneled sections ---------------------------------------

class _Capture:
    """Records constructor kwargs; never touches a network."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def disconnect(self):
        self.closed = True


@pytest.fixture
def tunneled_dbmanager(tmp_path, fake_ssh, monkeypatch):
    """DBManager whose tunneled section rides the fake-ssh tunnel."""
    lp = _free_port()
    ini = tmp_path / "db.ini"
    ini.write_text(
        "[ssh:bastion]\n"
        "host = 127.0.0.1\n"
        "port = 2222\n"
        "user = tester\n"
        "key = /k\n"
        "\n"
        "[ck-tunneled]\n"
        "type = clickhouse\n"
        "tunnel = ssh:bastion\n"
        f"local_port = {lp}\n"
        "remote_host = 10.0.0.9\n"
        "remote_port = 9000\n"
        "host = should-be-ignored\n"
        "port = 1999\n"
        "user = chuser\n"
    )
    tm = tunnel.TunnelManager(config_file=str(ini))
    import connections.manager as cm
    monkeypatch.setattr(cm, "_get_tunnel_manager", lambda: tm)
    # LockedDBManager is what the MCP server actually builds — test that layer.
    return LockedDBManager(str(ini)), tm


def test_tunneled_client_connects_to_local_forward(tunneled_dbmanager, monkeypatch):
    m, tm = tunneled_dbmanager
    seen = {}
    monkeypatch.setattr("connections.manager.Client",
                        lambda **kw: seen.update(kw) or _Capture(**kw))
    client = m.ck_tunneled
    assert seen["host"] == "localhost"          # driver never sees the bastion
    assert seen["port"] == tm.local_port_for("ck-tunneled")
    assert seen["user"] == "chuser"
    assert seen["connect_timeout"] == 10        # default timeouts still injected
    for meta in ("tunnel", "remote_host", "remote_port", "local_port", "type"):
        assert meta not in seen                 # meta keys stripped, not driver kwargs


def test_tunneled_client_cached_until_tunnel_generation_bumps(tunneled_dbmanager, monkeypatch):
    m, tm = tunneled_dbmanager
    made = []
    monkeypatch.setattr("connections.manager.Client",
                        lambda **kw: made.append(_Capture(**kw)) or made[-1])
    c1 = m.ck_tunneled
    assert m.ck_tunneled is c1                  # cached while tunnel generation holds
    tm._procs["bastion"].kill()
    tm._procs["bastion"].wait()
    c2 = m.ck_tunneled                          # __getattr__ re-ensures -> restart -> gen bump
    assert c2 is not c1
    assert tm.generation("bastion") == 2


def test_locked_manager_wraps_tunneled_client(tunneled_dbmanager):
    from connections.wrappers import LockedClickHouseClient
    m, _ = tunneled_dbmanager
    client = m.ck_tunneled
    assert isinstance(client, LockedClickHouseClient)


def test_getattr_unknown_section_raises(tmp_path):
    ini = tmp_path / "db.ini"
    ini.write_text("[other]\ntype = mysql\nhost = h\n")
    with pytest.raises(AttributeError, match="ck-nope"):
        DBManager(str(ini)).ck_nope


def test_importing_tunnel_first_leaves_helpers_intact():
    """Regression: `import tunnel` before `connections` used to trip a cycle.

    tunnel -> connections.config_util -> connections/__init__ -> manager ->
    `from tunnel import get_manager` (while tunnel was mid-init) raised
    ImportError, which tunnel.py swallowed — silently leaving _read_config
    and _resolve_env as None and breaking every later TunnelManager with
    `TypeError: 'NoneType' object is not callable`. manager.py now defers
    its tunnel import, so both orders must yield a fully-wired module.
    """
    code = (
        "import sys, tempfile, os;"
        "sys.path.insert(0, sys.argv[1]);"
        "import tunnel;"  # tunnel FIRST — the previously broken order
        "assert tunnel._read_config is not None, 'read_config unresolved';"
        "assert tunnel._resolve_env is not None, 'resolve_env unresolved';"
        "ini = tempfile.NamedTemporaryFile(mode='w', suffix='.ini', delete=False);"
        "ini.write('[ssh:b]\\nhost = h\\nuser = u\\nkey = /k\\n');"
        "ini.close();"
        "m = tunnel.TunnelManager(config_file=ini.name);"
        "m._load();"
        "assert m._blocks == {'b': {'host': 'h', 'user': 'u', 'key': '/k'}};"
        "import connections;"
        "assert connections.DBManager is not None"
    )
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-c", code, repo_root], check=True,
                   capture_output=True, text=True, timeout=60)


# --- standalone runner --------------------------------------------------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
