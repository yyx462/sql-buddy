# SQL Buddy

A read-only **MCP server** that lets your AI agent (Claude Code, and any
MCP-compatible client) safely query your **MySQL** and **ClickHouse** databases.

Your agent asks in plain language; SQL Buddy runs the SQL, enforces **read-only**
(no `INSERT`/`UPDATE`/`DROP` ever reaches your data), adds safety `LIMIT`s, and
caches results. You stay in control — the agent can look, never touch.

```
Your AI agent  ──MCP──►  SQL Buddy (runs locally)  ──►  your databases
                          • opens its own SSH tunnel through a bastion
                          • read-only guard · auto LIMIT · schema lookup
```

SQL Buddy runs **locally** (a small Python program, not a cloud service). It
**opens the SSH tunnel itself** from a bastion + key + port mapping in `db.ini`
— no separate `ssh -N` terminal, and your credentials never leave your machine.

---

## Prerequisites

- **Python 3.11+** — `python3 --version`
- **[uv](https://docs.astral.sh/uv/)** (recommended) or plain `pip`.
- **SSH access** to a bastion/jump-host that can reach your databases: its
  `user@host` + ssh `port` (often a high port like `22222`, **not** 22), a
  **private key file**, and each DB's `host:port` **as seen from the bastion**.
- **Read-only DB credentials** — a user that can `SELECT` but not write.
- An **MCP-compatible client** (e.g. Claude Code, **Trae**).
- **Windows only (Auto tunnels):** [OpenSSH Client](https://learn.microsoft.com/windows-server/administration/openssh/openssh_install_firstuse).
  (Skip if you run the tunnel yourself in another terminal.)

> Deep notes on tunnels, Windows/Trae, and every `db.ini` field live in
> [`docs/setup.md`](docs/setup.md) and [`docs/windows-trae.md`](docs/windows-trae.md).

---

## Quickstart

**Install** (current release **0.3.3**, Python ≥ 3.11):

```bash
# From PyPI (recommended):
pip install sqlbuddy-nwi          # provides the `sql-buddy` command
# …or run the latest instantly without installing:
uvx --from sqlbuddy-nwi sql-buddy
```

```bash
# From source (for development):
git clone https://github.com/yyx462/sql-buddy && cd sql-buddy
uv run server.py                  # auto-provisions an isolated .venv, then serves
```

**Configure & connect:**

```bash
sql-buddy init                    # interactive wizard → writes connections/config/db.ini
sql-buddy doctor --connect        # green-checks db.ini, tunnels, MCP client config
sql-buddy mcp add                 # registers sql-buddy with Claude Code (writes .mcp.json)
```

> From a **git checkout**, prefix these with `uv run` (e.g. `uv run sql-buddy init`).
> From a **pip / uvx install**, run `sql-buddy` directly — it's on your PATH.

Restart your client, then ask your agent *"list the tables in the `ck`
connection"* — if it answers, you're live. 🎉

> **Bare `sql-buddy` (no command) runs the MCP server** — that's what your AI
> client invokes, so configs hold no fragile absolute `.venv/bin/python` path.
> `sql-buddy mcp add` auto-detects how you installed and writes the right
> command: `uvx --from sqlbuddy-nwi sql-buddy` for a pip/uvx install,
> `uv --directory … run server.py` for a checkout.
> Other commands: `version`, `doctor [--connect]`, `mcp print|add`, `update-check`, `init`.
> On **Windows**, see [`docs/windows-trae.md`](docs/windows-trae.md) and use
> `sql-buddy mcp add --client trae`.

---

## Configure connections (`db.ini`)

Run the wizard (easiest) or copy the template:

```bash
sql-buddy init
# or, from a git checkout, copy the template and edit by hand:
cp connections/config/db.ini.example connections/config/db.ini
$EDITOR connections/config/db.ini
```

Each database is one **section**; the `[bracketed]` name becomes the
**connection name** your agent uses (e.g. `ck`, `mysql`, `prod-read`).

```ini
# db.ini  —  NEVER commit this file (gitignored). It holds live credentials.

[ssh:prod]
host = bastion.example.com
port = 22
user = your_ssh_user
key  = ~/.ssh/id_ed25519          # path to your private key (~ expanded)

[ck]
type        = clickhouse
tunnel      = ssh:prod            # self-managed tunnel through the bastion above
remote_host = 10.0.0.21           # DB host AS SEEN FROM the bastion
remote_port = 9000                # DB port AS SEEN FROM the bastion
local_port  = 10021               # localhost port the driver connects to
user        = your_readonly_user
password    = your_password
database    = default

[local-db]                        # direct (non-tunneled) connection also works
type     = mysql
host     = localhost
port     = 3306
user     = your_readonly_user
password = your_password
database = myapp
```

`remote_host`/`remote_port` are the DB **as seen from the bastion**, not your
laptop — getting this wrong is the #1 setup failure. For the full field
reference, SSH troubleshooting, and External (`ssh -N`) mode, see
[`docs/setup.md`](docs/setup.md).

🔒 Double-check `git status` does **not** list `db.ini` before any commit.

---

## Connect your AI agent

```bash
sql-buddy mcp add                      # Claude Code project → ./.mcp.json
sql-buddy mcp add --scope user         # Claude Code user    → ~/.claude.json
sql-buddy mcp add --client trae        # Trae project        → ./.trae/mcp.json
sql-buddy mcp add --client trae --scope user   # Trae user-global
```

Restart your client. Preview the exact block first with `sql-buddy mcp print`
(paths filled in):

```json
{
  "mcpServers": {
    "sql-buddy": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/sql-buddy", "run", "server.py"]
    }
  }
}
```

> No `uv` on the machine that runs your MCP client? `sql-buddy mcp print --python`
> sets `command` to the running interpreter (`sys.executable`).
> For other MCP clients (Cursor, Continue, WorkBuddy, …) and Windows/Trae
> specifics, see [`docs/setup.md`](docs/setup.md) and [`docs/windows-trae.md`](docs/windows-trae.md).

---

## Security model (short)

- **Read-only enforced in code**, before any SQL reaches the network. Write
  keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, …) are
  rejected by a guard layer wrapping every connection.
- **Credentials stay local** — in `db.ini` on your machine, never sent anywhere.
- **SSH tunnels bind to localhost only** (`127.0.0.1`); key-based auth only.
- **`LIMIT` is always applied** to `SELECT`/`WITH` (default 10, hard cap 2000).

Full security posture and an error/troubleshooting matrix:
[`docs/reference.md`](docs/reference.md).

---

## Documentation

| Topic | Where |
|-------|-------|
| Full setup, SSH tunnels, `db.ini` field reference, other MCP clients, agent-install rules | [`docs/setup.md`](docs/setup.md) |
| Windows & Trae IDE notes | [`docs/windows-trae.md`](docs/windows-trae.md) |
| MCP tools, configuration vars, troubleshooting, security reference | [`docs/reference.md`](docs/reference.md) |
| Domain language (Connection vs database, read-only invariant) | [`UBIQUITOUS_LANGUAGE.md`](UBIQUITOUS_LANGUAGE.md) |
| Contributing & the review rubric | [`CONTRIBUTING.md`](CONTRIBUTING.md) · [`AGENTS.md`](AGENTS.md) |
| Architecture decisions (ADRs) | [`docs/adr/`](docs/adr/) |
