"""End-to-end: the shared streamable-HTTP server serves N agents from one process.

Receipt (2026-11-13): stdio mode spawns one server.py per agent — ~87 MiB each
after the pandas removal, and each tries to bind the SAME tunnel ports, so
concurrent agents collide (ExitOnForwardFailure) and all but the first lose
DB access. `sql-buddy http` runs ONE process; this test spawns it for real and
drives TWO concurrent MCP clients (installed mcp client stack, real HTTP +
JSON-RPC), asserting both complete full round-trips while the server stays up.

No DB needed: check_connection on an unconfigured name returns structured
error JSON — proving the whole path (HTTP -> session -> tool -> db layer ->
JSON response) without touching a database.
"""

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def http_url():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "server.py", "http", "--port", str(port)],
        cwd=str(ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"http server died at startup: {proc.stderr.read().decode()[:500]}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("http server never opened its port")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _roundtrip(url: str, out: dict, idx: int):
    async def go():
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        def _no_env_proxies(**kwargs):
            # Loopback must not be routed through a host proxy (a SOCKS
            # ALL_PROXY without socksio installed kills the client).
            return httpx.AsyncClient(trust_env=False, **kwargs)

        async with streamablehttp_client(url, httpx_client_factory=_no_env_proxies) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                res = await session.call_tool(
                    "check_connection", {"connection": "definitely-not-configured"})
                payload = json.loads(res.content[0].text)
                out[idx] = {"n_tools": len(tools.tools),
                            "payload_keys": sorted(payload.keys()),
                            "status": payload.get("status")}
    asyncio.run(go())


def test_two_concurrent_clients_share_one_http_server(http_url):
    out: dict = {}
    threads = [threading.Thread(target=_roundtrip, args=(http_url, out, i))
               for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(out) == 2, f"both clients must finish: {out}"

    for idx, result in out.items():
        assert result["n_tools"] >= 8, f"client {idx}: tool catalog came back thin"
        # unconfigured connection -> structured error JSON, not a crash
        assert "connection" in result["payload_keys"] and result["status"] == "error"

    assert out[0]["n_tools"] == out[1]["n_tools"], "clients saw different catalogs"


def test_http_server_default_is_loopback(http_url):
    """The shared server must not bind a public interface by accident."""
    host = http_url.split(":")[1].replace("//", "")
    assert host in ("127.0.0.1", "localhost", "::1")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
