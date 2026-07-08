# SQL Buddy

A read-only **MCP server** that lets your AI agent (Claude Code, and any MCP-compatible
client) safely query your **MySQL** and **ClickHouse** databases.

Your agent asks questions in plain language; SQL Buddy runs the SQL, enforces
**read-only** (no `INSERT`/`UPDATE`/`DROP` ever reaches your data), adds safety
`LIMIT`s, and caches results. You stay in control - the agent can look, never touch.

---

## How it works (30-second mental model)

```
Your AI agent  ──MCP──►  SQL Buddy (runs on your laptop)  ──►  your databases
                          • opens its own SSH tunnel            (behind a bastion)
                          • read-only guard
                          • auto LIMIT
                          • schema / table lookup
```

- **SQL Buddy runs locally** on your machine (or a server you control). It is a small
  Python program, not a cloud service.
- **It opens the SSH tunnel itself.** You give it the bastion, your key file, and a
  per-database port mapping in `db.ini` - no separate `ssh -N` terminal to babysit.
  Your database credentials never leave your machine.
- **It is read-only by design.** A guard layer rejects every write keyword before it
  ever touches the network.

---

## Prerequisites

You will need:

1. **Python 3.11+** - `python3 --version`
2. **[uv](https://docs.astral.sh/uv/)** (fast Python installer) - or plain `pip` works too.
3. **SSH access** to a bastion/jump-host that can reach your databases:
   - the bastion `user@host` and ssh `port`,
   - a **private key file** on your machine (key-based auth only),
   - for each database, the DB `host:port` **as seen from the bastion**.
4. **Read-only database credentials** - ask your DBA for a user that can `SELECT` but not write.
5. An **MCP-compatible client** to connect it to (e.g. Claude Code).

---

## Setup

### Step 1: Install SQL Buddy

```bash
git clone <this-repo-url> sql-buddy
cd sql-buddy

# Create an isolated Python environment and install
uv venv .venv --python 3.11
uv pip install -e .
```

> No `uv`? You can use plain pip instead:
> ```bash
> python3.11 -m venv .venv
> source .venv/bin/activate
> pip install -e .
> ```

Quick smoke test (starts the server, then `Ctrl+C` to stop):

```bash
.venv/bin/python server.py
```

If it prints no errors, the install worked. Stop it for now - we configure databases next.

---

### Step 2: Reach your databases (SSH tunnel - managed by SQL Buddy)

Most databases are **not exposed to the public internet** - they sit behind a firewall
and are only reachable from inside the network. SQL Buddy reaches them by opening an
**SSH tunnel** itself: a secure pipe that maps a **port on your laptop** to the
**database port on the remote server**, through a bastion/jump-host you can SSH into.

You do **not** run a separate `ssh -N` terminal. SQL Buddy starts the tunnel lazily on
the first query that needs it, keeps it alive for the life of the server, restarts it
if it drops, and tears it down when the server exits. One bastion -> one tunnel, even
with many databases behind it.

You describe the tunnel in `db.ini` (next step) with:

- an **`[ssh:<name>]`** section for the bastion (host, port, user, key file), and
- a **`tunnel = ssh:<name>`** line on each database connection, plus its
  `remote_host` / `remote_port` (the DB as seen from the bastion) and `local_port`
  (the port SQL Buddy binds on localhost).

The local port is **any free port** between 1024 and 65535 - pick numbers you can
remember and that nothing else on your machine is using. The remote port must match
the real database port (ClickHouse default **9000**, MySQL default **3306**).

> **First connection only:** SQL Buddy uses `StrictHostKeyChecking=accept-new`, so the
> bastion's host key is auto-trusted on first run. If you prefer to vet it manually,
> `ssh you@bastion` once first to add it to `~/.ssh/known_hosts`.

---

### Step 3: Add your credentials (`db.ini`)

Easiest path - run the interactive wizard. It asks whether sql-buddy should open
the SSH tunnel itself (**Auto**) or connect to a tunnel you already run elsewhere
(**External**, e.g. an `ssh -N` terminal on Windows), then writes `db.ini` for you:

```bash
.venv/bin/python init.py
```

Run it once when setting up, or again to switch modes - sql-buddy never prompts
during normal use (the choice persists in `db.ini`).

Or do it by hand - copy the template, then edit it:

```bash
cp db_conn_locked/config/db.ini.example db_conn_locked/config/db.ini
$EDITOR db_conn_locked/config/db.ini
```

Each database is one **section** in this file. A section's name (the `[bracketed]` word)
becomes the **connection name** your agent uses (e.g. `ck`, `mysql`, `prod-read`).

```ini
# db.ini  -  NEVER commit this file. It is gitignored for a reason.

# --- bastion ---
[ssh:prod]
host = bastion.example.com
port = 22
user = your_ssh_user
key  = ~/.ssh/id_ed25519          # path to your private key (~ expanded)
server_alive_interval = 30        # optional keepalive (default 30)
server_alive_count_max = 3        # optional (default 3)

# --- a tunneled ClickHouse ---
[ck]
type        = clickhouse
tunnel      = ssh:prod            # opt into the self-managed tunnel
remote_host = 10.0.0.21           # DB host AS SEEN FROM the bastion
remote_port = 9000                # DB port AS SEEN FROM the bastion
local_port  = 10021               # localhost port == what the driver connects to
user        = your_readonly_user
password    = your_password
database    = default

# --- a tunneled MySQL ---
[mysql]
type        = mysql
tunnel      = ssh:prod
remote_host = 10.0.0.95
remote_port = 3306
local_port  = 10095
user        = your_readonly_user
password    = your_password
database    = myapp

# --- a direct (non-tunneled) connection still works ---
[local-db]
type     = mysql
host     = localhost
port     = 3306
user     = your_readonly_user
password = your_password
database = myapp
```

**Field reference:**

| Field | Where | Required | Notes |
|-------|-------|----------|-------|
| `type` | connection | yes | `clickhouse` or `mysql`. (If omitted, guessed from the section name: `ck*`->clickhouse, `db*`/`mysql*`->mysql. Explicit is safer.) |
| `tunnel` | connection | no | `ssh:<name>` to route through the matching `[ssh:<name>]` bastion. Omit for a direct connection. |
| `remote_host` | connection | if `tunnel` set | DB host as seen **from the bastion** (inside the network). |
| `remote_port` | connection | if `tunnel` set | DB port as seen from the bastion. |
| `local_port` | connection | if `tunnel` set | Port bound on localhost; the DB driver connects to `localhost:local_port`. |
| `host` | connection | direct only | Connect target for non-tunneled connections (almost always `localhost`). Ignored when `tunnel` is set. |
| `port` | connection | direct only | Connect target port for non-tunneled connections. Ignored when `tunnel` is set. |
| `user` | connection | yes | A read-only database user. Ask your DBA. |
| `password` | connection | yes | That user's password. |
| `database` | connection | ClickHouse | The default database/schema to query. |
| `host` | `[ssh:*]` | yes | The bastion/jump-host you SSH into. |
| `port` | `[ssh:*]` | no | SSH port (default 22). |
| `user` | `[ssh:*]` | yes | SSH login user on the bastion. |
| `key` | `[ssh:*]` | yes | Path to your private key file (`~` is expanded). Key-based auth only. |
| `server_alive_interval` | `[ssh:*]` | no | SSH keepalive ping seconds (default 30). |
| `server_alive_count_max` | `[ssh:*]` | no | Drop the tunnel after this many missed pings (default 3). |

> **Naming your connections:** both `my_db` and `my-db` work - underscores and dashes are
> treated interchangeably. So `connection="ck_amazon"` and `connection="ck-amazon"` are the same.

🔒 **Security:** `db.ini` is listed in `.gitignore` and must never be committed. Double-check
with `git status` before pushing - it should never appear as a tracked file.

---

### Step 4: Test it works

Point SQL Buddy at your databases:

```bash
# List the connections SQL Buddy found in db.ini
.venv/bin/python -c "import db_query; print(db_query.list_connections())"

# Run a real read query - this opens the SSH tunnel on first use
.venv/bin/python -c "import db_query; print(db_query.execute_limit_query('SELECT 1','ck'))"

# (optional) Set which connection is used when the agent doesn't specify one
export SQL_BUDDY_DEFAULT_CONNECTION=ck
```

If the query returns rows, the tunnel came up and you're ready to wire it into your AI agent.

---

### Step 5: Connect your AI agent (MCP config)

Add SQL Buddy to your MCP client's config. For **Claude Code**, edit
`.claude/settings.json` (project or global) and replace the paths with **your** real ones:

```json
{
  "mcpServers": {
    "sql-buddy": {
      "command": "/absolute/path/to/sql-buddy/.venv/bin/python",
      "args": ["/absolute/path/to/sql-buddy/server.py"]
    }
  }
}
```

Then restart your client. Ask your agent something like *"list the tables in the `ck`
connection"* - if it can answer, you're live. 🎉

---

## 👉 For AI agents: setting this up for a user

If you (the agent) are installing SQL Buddy on a user's machine, **do not guess** any
value below. Ask the user explicitly, one at a time, in plain language.

> The `init.py` wizard is interactive (for humans). As an agent, **write `db.ini`
> directly** with your file-editing tool instead of driving the wizard - it's faster
> and avoids pasting secrets through a prompt.

1. **The repository** - clone it, then `uv venv .venv --python 3.11 && uv pip install -e .`
   inside it.
2. **Ask the user which mode** they want:
   - **Auto** - sql-buddy opens the SSH tunnel itself. Continue to step 3.
   - **External** - the user already runs a tunnel/connection (e.g. `ssh -N` on Windows).
     Skip to step 4 (External branch).
3. **Auto mode - SSH bastion** they reach their databases through. Collect:
   - the bastion `host` and ssh `port` (often 22),
   - the ssh `user`,
   - the **path to their private key file** on this machine,
   - (optional) keepalive settings (defaults of 30s/3 are fine).
4. **For each database** the user wants to query, collect:
   - A **connection name** (a short label, e.g. `ck`, `mysql`, `prod`). Use `_` or `-`.
   - The **DBMS type**: `clickhouse` or `mysql`.
   - **Auto mode:** the DB `host`/`port` as seen **from the bastion**
     (`remote_host`/`remote_port`) - confirm with the user; this is not their laptop's
     view - plus a free **`local_port`** on the laptop.
   - **External mode:** the `host`/`port` to connect to (usually `localhost` + the local
     tunnel port). No `tunnel`/`remote_*`/`local_port` keys.
   - The **read-only database user** and **password**.
   - The default **database** name (ClickHouse) - optional for MySQL.
5. **Write `db_conn_locked/config/db.ini`** (see `db.ini.example` for the shape):
   - Auto: one `[ssh:*]` section for the bastion + each connection with
     `tunnel = ssh:*` and its `remote_host`/`remote_port`/`local_port` (no `host`/`port`).
   - External: each connection with `host`/`port`/`user`/`password` (and `database` for
     ClickHouse). No `[ssh:*]` block.
6. **Verify** with
   `python -c "import db_query; print(db_query.check_connection_query('<conn>'))"`.
   Expect `{"status":"ok","latency_ms":...}`. On `status:'error'`, read the `hint` and
   follow it (in External mode an error usually means the user's tunnel is down).
7. **Confirm `db.ini` is not tracked** by git (`git status` should not list it).
8. **Register the MCP server** in the user's client config with absolute paths to their
   `.venv/bin/python` and `server.py`.

Never write credentials to anywhere except `db.ini`. Never commit `db.ini`. When using
the running server, every tool error JSON carries a `hint` - follow it rather than
guessing.

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `query(sql, connection)` | Execute read-only SQL. `LIMIT` auto-added if missing (default 10, max 2000); pass an explicit `LIMIT N` for more rows. Returns JSON. |
| `find_table(search, limit?)` | **Locate a table across ALL connections by name closeness** when you're unsure which connection holds it, or after a wrong-connection error. Returns `connection` + `database` + `qualified_name` to query next. |
| `get_table_definition(table_name, connection)` | Get `CREATE TABLE` with parsed columns, engine, partition. Cached 3 days. |
| `get_info(table_name, connection)` | Get row count, indexes (MySQL) / partitions (ClickHouse). Cached 3 days. |
| `list_tables(connection, database?, like?)` | List tables; always pass `like` to search server-side (fast). |
| `list_connections()` | Discover available connections from `db.ini`. |
| `check_connection(connection)` | **Ping one connection** (`SELECT 1` round-trip). Returns `{status:'ok', latency_ms}` or `{status:'error', hint}`. Use to diagnose a dead tunnel/conn before running real queries. |
| `get_history(connection, table_name?)` | Past query execution history, optionally filtered by table. |
| `explain(sql, connection)` | Run `EXPLAIN`; returns plan + cost estimates. |

All tools are **read-only**. `connection` names a `db.ini` section; `database` (in
`list_tables`) names a table namespace *inside* that server.

**Errors are agent-actionable.** Every error JSON includes the `connection` that
failed (when relevant) and a `hint` field with the suggested next action - an unknown
connection hints at `list_connections`; a transport error hints at `check_connection`
(and, in External tunnel mode, that the user's SSH tunnel may be down); a missing
table hints at `find_table`. Agents should read and follow the `hint`.

---

## Configuration reference

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SQL_BUDDY_DEFAULT_CONNECTION` | `ck` | Connection used when a tool's `connection` arg is omitted. |
| `SQL_BUDDY_RESOURCES` | *(unset)* | Optional JSON mapping `{connection: [table, ...]}` exposing selected table schemas as MCP resources. Advanced. |
| `DB_QUERY_HISTORY_DIR` | `./database` | Where query history + caches are stored. |

### File layout

```
db_conn_locked/config/db.ini          # your credentials + ssh blocks (gitignored, never commit)
db_conn_locked/config/db.ini.example  # template (committed)
database/                             # caches + query history (gitignored)
server.py                             # MCP server entrypoint
db_query.py                           # query logic
tunnel.py                             # self-managed SSH tunnels (one ssh -N per [ssh:*] block)
init.py                               # interactive setup wizard (writes db.ini)
```

---

## Troubleshooting

**"SSH tunnel did not come up" / "ssh stderr: ..."**
-> SQL Buddy could not establish the tunnel. Common causes: wrong `key` path (or
unreadable key), wrong bastion `host`/`port`/`user`, the bastion host key changed, or a
`local_port` already in use by something else. The error message includes the ssh stderr
tail. Test the SSH leg manually with the same details:
`ssh -N -p <port> -i <key> <user>@<host> -L 127.0.0.1:<local_port>:<remote_host>:<remote_port>`.

**"Connection refused" / "timed out" when the agent queries anything**
-> The tunnel came up but the DB target is wrong. Check `remote_host`/`remote_port` -
they must be the DB's address **as seen from the bastion**, not from your laptop.

**"Unknown connection" / wrong connection used**
-> The `connection` name must match a `[section]` in `db.ini`. Run
`list_connections()` to see what's configured. Check `SQL_BUDDY_DEFAULT_CONNECTION`.

**"Write operation blocked by db_conn_locked"**
-> This is **working as intended.** SQL Buddy rejected a mutating statement. Use a
different tool if you genuinely need to change data - SQL Buddy cannot do it.

**Agent keeps querying the wrong database for a table**
-> Have it call `find_table("table_name")` first - it searches every connection and reports
where the table actually lives.

**An `ssh -N` process is still running after SQL Buddy exited**
-> On a graceful exit SQL Buddy kills its tunnels. A hard kill (`SIGKILL` / power loss)
can orphan the `ssh` process; it is harmless (idle) but will hold the local ports. Reclaim
them with `pkill -f 'ssh -N .*127.0.0.1:<local_port>'` or `pkill -af 'ssh -N'`.

**`db.ini` shows up in `git status` as a new/changed file**
-> Stop. Do **not** commit it. It should be gitignored. If it's already tracked, remove it
from git history (it contains live credentials):
```bash
git rm --cached db_conn_locked/config/db.ini
```

---

## Security model

- **Read-only enforced in code**, before any SQL reaches the network. Write keywords
  (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, …) are rejected by a guard
  layer wrapping every connection.
- **Credentials stay local** - in `db.ini` on your machine, never sent to any third party.
- **SSH tunnels bind to localhost only** (`127.0.0.1`) - the forwarded DB ports are not
  exposed on your LAN. Key-based auth is used for the SSH leg; the key never leaves your
  machine.
- **`LIMIT` is always applied** to `SELECT`/`WITH` (default 10, hard cap 2000) to prevent
  accidental huge scans.
- **`db.ini` is gitignored.** Verify with `git status` before every push.
