"""Self-contained SSH tunnel manager for sql-buddy.

Reads `[ssh:<name>]` bastion blocks and per-connection tunnel mappings from
`db.ini`, spawns ONE `ssh -N` subprocess per bastion (lazy, on first use), keeps
it supervised for the lifetime of the process, and tears it down at exit.

Key-based auth only (the `-i <keyfile>` path). One bastion -> one tunnel, even
when many DB connections sit behind it: every connection referencing a given
`[ssh:*]` block contributes a `-L` forward to that single subprocess.

A DB connection opts in like this::

    [ssh:prod]
    host = 192.0.2.10
    port = 22222
    user = secret
    key  = ~/.ssh/prod_key

    [ck]
    tunnel      = ssh:prod
    remote_host = 10.0.0.21     # DB host AS SEEN FROM the bastion
    remote_port = 9000             # DB port AS SEEN FROM the bastion
    local_port  = 10021            # localhost port ssh binds == driver connect port

The DB driver then connects to `localhost:local_port`; it never knows about SSH.
"""

import atexit
import configparser
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Import shared config helpers from the connections package, avoiding any
# circular-import risk by importing from the leaf module rather than the
# connections __init__ (which imports DBManager -> tunnel).
try:
    from connections.config_util import (
        read_config as _read_config,
        resolve_env as _resolve_env,
        config_path as _config_path,
    )
except ImportError:
    _resolve_env = None
    _read_config = None
    _config_path = None


_CONFIG_FILE = (_config_path() if _config_path
                else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'connections', 'config', 'db.ini'))

# ssh defaults
DEFAULT_SSH_PORT = 22
DEFAULT_ALIVE_INTERVAL = 30
DEFAULT_ALIVE_COUNT_MAX = 3

# How long to wait (s) for freshly-forwarded local ports to accept connections.
PORT_READY_TIMEOUT = 15.0
PORT_READY_POLL_INTERVAL = 0.1
# Tail length kept for failure diagnostics.
STDERR_TAIL_LINES = 50


# --- config parsing -------------------------------------------------------

def _normalize(section: str) -> str:
    """Match the connection manager's `_`/`-` interchangeability for connection names."""
    return section.replace('_', '-')


def _ssh_blocks(cfg) -> dict:
    """Return {block_name: section_dict} for every `[ssh:*]` section."""
    blocks = {}
    prefix = 'ssh:'
    for section in cfg.sections():
        if section.lower().startswith(prefix):
            blocks[section[len(prefix):]] = dict(cfg[section])
    return blocks


def _conn_tunnels(cfg) -> dict:
    """Return {conn_section: (block, local_port, remote_host, remote_port)}
    for every DB connection that declares a `tunnel` key. Raises on misconfig."""
    out = {}
    for section in cfg.sections():
        if section.lower().startswith('ssh:'):
            continue
        if not cfg.has_option(section, 'tunnel'):
            continue
        block = cfg.get(section, 'tunnel').strip()
        if block.lower().startswith('ssh:'):
            block = block[4:]  # `[ssh:prod]` section -> internal block name 'prod'
        try:
            local_port = int(cfg.get(section, 'local_port'))
            remote_port = int(cfg.get(section, 'remote_port'))
        except (configparser.NoOptionError, ValueError) as e:
            raise ValueError(
                f"[{section}] declares tunnel='{block}' but needs integer "
                f"`local_port` and `remote_port` ({e})"
            )
        if not cfg.has_option(section, 'remote_host'):
            raise ValueError(
                f"[{section}] declares tunnel='{block}' but is missing `remote_host`"
            )
        remote_host = cfg.get(section, 'remote_host').strip()
        out[section] = (block, local_port, remote_host, remote_port)
    return out


def _port_open(port: int, host: str = '127.0.0.1', timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --- manager --------------------------------------------------------------

class TunnelManager:
    """Owns the ssh subprocesses for this process. Thread-safe singleton via get_manager()."""

    def __init__(self, config_file: str = None):
        self._config_file = config_file or _CONFIG_FILE
        self._lock = threading.RLock()
        self._cfg = None
        self._blocks: dict = {}            # {block: section params}
        self._conn_tunnels: dict = {}      # {conn_section: (block, lp, rh, rp)}
        self._procs: dict = {}             # {block: Popen}
        self._gen: dict = {}               # {block: generation int}
        self._stderr_tail: dict = {}       # {block: [lines]}
        self._loaded = False
        self._atexit_registered = False

    # -- config --
    def _load(self):
        if self._loaded:
            return
        cfg = _read_config(self._config_file)
        self._cfg = cfg
        self._blocks = _ssh_blocks(cfg)
        self._conn_tunnels = _conn_tunnels(cfg)
        self._loaded = True
        if not self._atexit_registered:
            atexit.register(self.stop_all)
            self._atexit_registered = True

    def reload(self):
        """Force a re-read of db.ini (e.g. after the user edits it)."""
        with self._lock:
            self._loaded = False
            self._load()

    # -- introspection (used by the connection manager) --
    def tunnel_for(self, conn_section: str):
        """Return (block, local_port, remote_host, remote_port) or None if not tunneled."""
        self._load()
        return self._conn_tunnels.get(_normalize(conn_section))

    def block_for(self, conn_section: str):
        """Return the ssh block name for a connection, or None if it isn't tunneled."""
        t = self.tunnel_for(conn_section)
        return t[0] if t else None

    def local_port_for(self, conn_section: str):
        """Return the local forward port for a tunneled connection, or None."""
        t = self.tunnel_for(conn_section)
        return t[1] if t else None

    def generation(self, block: str) -> int:
        """Bumped each time this block's tunnel is (re)started. 0 if never started."""
        with self._lock:
            return self._gen.get(block, 0)

    # -- lifecycle --
    def ensure_for(self, conn_section: str):
        """Make sure the bastion tunnel serving conn_section is up. Returns block or None."""
        block = self.block_for(conn_section)
        if block is None:
            return None
        self._ensure_block(block)
        return block

    def _forwards_for(self, block: str) -> list:
        """All (local_port, remote_host, remote_port) tuples using `block`."""
        return [
            (lp, rh, rp)
            for (b, lp, rh, rp) in self._conn_tunnels.values()
            if b == block
        ]

    def _build_cmd(self, block: str, forwards: list) -> list:
        p = self._blocks[block]
        # expanduser handles ~/… and ~\… on Windows; normpath fixes mixed slashes.
        key = os.path.normpath(os.path.expanduser(_resolve_env(p['key'])))
        ssh_port = int(_resolve_env(p.get('port', DEFAULT_SSH_PORT)))
        alive_int = _resolve_env(p.get('server_alive_interval', DEFAULT_ALIVE_INTERVAL))
        alive_max = _resolve_env(p.get('server_alive_count_max', DEFAULT_ALIVE_COUNT_MAX))
        user, host = _resolve_env(p['user']), _resolve_env(p['host'])

        # Prefer the resolved binary so GUI-spawned MCP (Trae) finds OpenSSH even
        # when the shell PATH differs from the IDE's PATH.
        ssh_bin = shutil.which('ssh') or 'ssh'
        cmd = [
            ssh_bin, '-N',
            '-o', f'ServerAliveInterval={alive_int}',
            '-o', f'ServerAliveCountMax={alive_max}',
            '-o', 'ExitOnForwardFailure=yes',     # die loudly on a port collision
            '-o', 'StrictHostKeyChecking=accept-new',  # no first-run prompt hang
            '-p', str(ssh_port),
            '-i', key,
        ]
        for lp, rh, rp in forwards:
            cmd += ['-L', f'127.0.0.1:{lp}:{rh}:{rp}']  # localhost-only bind
        cmd.append(f'{user}@{host}')
        return cmd

    def _popen_kwargs(self) -> dict:
        """Subprocess flags that work under GUI MCP hosts (esp. Windows/Trae)."""
        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.PIPE,
        }
        if sys.platform == "win32":
            # CREATE_NO_WINDOW: no flashing console when Trae/Claude spawns us.
            # CREATE_NEW_PROCESS_GROUP: detach from parent's console ctrl-events.
            flags = 0
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if flags:
                kwargs["creationflags"] = flags
        else:
            # Own process group -> survives parent's signal storm.
            kwargs["start_new_session"] = True
        return kwargs

    def _ensure_block(self, block: str):
        self._load()
        if block not in self._blocks:
            raise ValueError(
                f"Unknown ssh block '{block}'. Define an [ssh:{block}] section in db.ini."
            )
        forwards = self._forwards_for(block)
        if not forwards:
            return  # nothing references this block yet -> nothing to forward

        with self._lock:
            proc = self._procs.get(block)
            if proc is not None and proc.poll() is None:
                return  # already alive

            cmd = self._build_cmd(block, forwards)
            logger.info("Starting SSH tunnel for [%s]: %s", block, ' '.join(cmd))
            try:
                proc = subprocess.Popen(cmd, **self._popen_kwargs())
            except FileNotFoundError as e:
                raise RuntimeError(
                    f"SSH binary not found while starting tunnel [{block}]. "
                    f"On Windows install OpenSSH Client (Settings → Apps → "
                    f"Optional features). Original error: {e}"
                ) from e
            self._procs[block] = proc
            self._stderr_tail[block] = []
            self._gen[block] = self._gen.get(block, 0) + 1
            self._start_drain(block, proc)

        ports = [lp for (lp, _rh, _rp) in forwards]
        if not self._wait_ready(block, proc, ports):
            err = self._read_stderr(block)
            self._terminate(block)
            raise RuntimeError(
                f"SSH tunnel for [{block}] did not come up on local ports {ports}. "
                f"ssh stderr: {err or '(empty)'}"
            )

    def _start_drain(self, block: str, proc: subprocess.Popen):
        """Continuously drain ssh stderr into a bounded tail (prevents pipe-buffer deadlock)."""
        def drain():
            stream = proc.stderr
            if stream is None:
                return
            for line in iter(stream.readline, ''):
                if isinstance(line, bytes):
                    line = line.decode('utf-8', errors='replace')
                with self._lock:
                    tail = self._stderr_tail.setdefault(block, [])
                    tail.append(line.rstrip())
                    if len(tail) > STDERR_TAIL_LINES:
                        del tail[:-STDERR_TAIL_LINES]
        threading.Thread(target=drain, name=f"ssh-drain:{block}", daemon=True).start()

    def _wait_ready(self, block: str, proc: subprocess.Popen, ports: list,
                    timeout: float = PORT_READY_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False  # ssh exited before ports opened
            if all(_port_open(p) for p in ports):
                return True
            time.sleep(PORT_READY_POLL_INTERVAL)
        return False

    def _read_stderr(self, block: str) -> str:
        with self._lock:
            return '\n'.join(self._stderr_tail.get(block, []))

    def _terminate(self, block: str):
        with self._lock:
            proc = self._procs.pop(block, None)
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def stop_all(self):
        """Tear down every tunnel. Registered via atexit."""
        with self._lock:
            blocks = list(self._procs.keys())
        for block in blocks:
            logger.info("Stopping SSH tunnel for [%s]", block)
            self._terminate(block)


# process-wide singleton
_manager: TunnelManager | None = None


def get_manager() -> TunnelManager:
    global _manager
    if _manager is None:
        _manager = TunnelManager()
    return _manager
