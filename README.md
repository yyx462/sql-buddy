# SQL Buddy

A read-only **MCP server** that lets your AI agent (Claude Code, and any MCP-compatible
client) safely query your **MySQL** and **ClickHouse** databases.

Your agent asks questions in plain language; SQL Buddy runs the SQL, enforces
**read-only** (no `INSERT`/`UPDATE`/`DROP` ever reaches your data), adds safety
`LIMIT`s, and caches results. You stay in control — the agent can look, never touch.

---

## How it works (30-second mental model)

```
Your AI agent  ──MCP──►  SQL Buddy (runs on your laptop)  ──►  your databases
                          • read-only guard                      (via SSH tunnel)
                          • auto LIMIT
                          • schema / table lookup
```

- **SQL Buddy runs locally** on your machine (or a server you control). It is a small
  Python program, not a cloud service.
- **It connects to your databases over an SSH tunnel** (see [Step 2](#step-2-reach-your-databases-ssh-tunnel)). Your database credentials never leave your machine.
- **It is read-only by design.** A guard layer rejects every write keyword before it
  ever touches the network.

---

## Prerequisites

You will need:

1. **Python 3.11+** — `python3 --version`
2. **[uv](https://docs.astral.sh/uv/)** (fast Python installer) — or plain `pip` works too.
3. **SSH access** to a host that can reach your databases (a bastion / jump box, or the
   DB server itself).
4. **Read-only database credentials** — ask your DBA for a user that can `SELECT` but not write.
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

If it prints no errors, the install worked. Stop it for now — we configure databases next.

---

### Step 2: Reach your databases (SSH tunnel)

Most databases are **not exposed to the public internet** — they sit behind a firewall
and are only reachable from inside the network. You reach them from your laptop by
opening an **SSH tunnel**: a secure pipe that maps a **port on your laptop** to the
**database port on the remote server**.

SQL Buddy always connects to `localhost`. It does not know about SSH — the tunnel makes
the remote database *appear* to be running on your laptop.

#### The one command you need

```bash
ssh -N -L 9000:db-host.internal:9000 you@bastion.example.com
```

What each piece means:

| Part | Meaning |
|------|---------|
| `ssh` | Open an SSH connection. |
| `-N` | "No remote command" — we only want the tunnel, not a shell. |
| `-L 9000:db-host.internal:9000` | **L**ocal forward: your laptop's port `9000` → the port `9000` on `db-host.internal`. |
| `you@bastion.example.com` | The SSH user + host you log in through (your bastion / jump box). |

After this runs, anything connecting to **`localhost:9000`** on your laptop is quietly
piped to **`db-host.internal:9000`** inside the network. That local port (`9000`) is
exactly what you put as `port` in `db.ini` in the next step.

> **Is the database running on the bastion itself?** Then the remote target is
> `localhost`: `ssh -N -L 9000:localhost:9000 you@bastion.example.com`.

#### Multiple databases? Add more `-L` flags

One tunnel can forward many ports — one per database:

```bash
ssh -N \
  -L 9000:db-host.internal:9000 \
  -L 3306:db-host.internal:3306 \
  you@bastion.example.com
```

#### Picking local ports

The local port (the first number) can be **any free port** between 1024 and 65535.
Pick numbers you can remember and that nothing else on your machine is using.
The remote port (the last number) must match the real database port
(ClickHouse default **9000**, MySQL default **3306**).

#### Verify the tunnel works

With the `ssh -N -L ...` command running in one terminal, open a second terminal and
check the port is listening on your laptop:

```bash
# ClickHouse (native protocol, port 9000)
nc -zv localhost 9000        # expect: "succeeded" / "open"

# MySQL (port 3306)
nc -zv localhost 3306
```

#### Keep the tunnel alive

Leave the `ssh -N -L ...` terminal open while you use SQL Buddy. For a permanent setup,
consider **[autossh](https://www.harding.motd.ca/autossh/)** or a systemd service so the
tunnel reconnects automatically.

---

### Step 3: Add your credentials (`db.ini`)

Copy the template, then edit it:

```bash
cp db_conn_locked/config/db.ini.example db_conn_locked/config/db.ini
$EDITOR db_conn_locked/config/db.ini
```

Each database is one **section** in this file. A section's name (the `[bracketed]` word)
becomes the **connection name** your agent uses (e.g. `ck`, `mysql`, `prod-read`).

```ini
# db.ini  —  NEVER commit this file. It is gitignored for a reason.

[ck]
type     = clickhouse
host     = localhost          # always localhost — the SSH tunnel does the real work
port     = 9000               # the LOCAL port from your ssh -L command
user     = your_readonly_user
password = your_password
database = default            # the ClickHouse database to use by default

[mysql]
type     = mysql
host     = localhost
port     = 3306               # the LOCAL port from your ssh -L command
user     = your_readonly_user
password = your_password
database = myapp
```

**Field reference:**

| Field | Required | Notes |
|-------|----------|-------|
| `type` | yes | `clickhouse` or `mysql`. (If omitted, it's guessed from the section name: `ck*`→clickhouse, `db*`/`mysql*`→mysql. Explicit is safer.) |
| `host` | yes | Almost always `localhost` — you connect through the tunnel, not directly. |
| `port` | yes | The **local** port from your `ssh -L` command (NOT the remote DB port — though they may be the same number). |
| `user` | yes | A read-only database user. Ask your DBA. |
| `password` | yes | That user's password. |
| `database` | for ClickHouse | The default database/schema to query. |

> **Naming your connections:** both `my_db` and `my-db` work — underscores and dashes are
> treated interchangeably. So `connection="ck_amazon"` and `connection="ck-amazon"` are the same.

🔒 **Security:** `db.ini` is listed in `.gitignore` and must never be committed. Double-check
with `git status` before pushing — it should never appear as a tracked file.

---

### Step 4: Test it works

Point SQL Buddy at your databases:

```bash
# List the connections SQL Buddy found in db.ini
.venv/bin/python -c "import db_query; print(db_query.list_connections())"

# (optional) Set which connection is used when the agent doesn't specify one
export SQL_BUDDY_DEFAULT_CONNECTION=ck
```

If your connections show up, you're ready to wire it into your AI agent.

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
connection"* — if it can answer, you're live. 🎉

---

## 👉 For AI agents: setting this up for a user

If you (the agent) are installing SQL Buddy on a user's machine, **do not guess** any
value below. Ask the user explicitly, one at a time, in plain language. You need:

1. **The repository** — clone it, then `uv venv .venv --python 3.11 && uv pip install -e .`
   inside it.
2. **For each database** the user wants to query, collect:
   - A **connection name** (a short label, e.g. `ck`, `mysql`, `prod`). Use `_` or `-`.
   - The **DBMS type**: `clickhouse` or `mysql`.
   - The **SSH details** to reach it: the bastion/jump host (`user@host`) they SSH through,
     the remote database host + port as seen *from that bastion*, and the **local port**
     they want to use on their laptop. Help them build the `ssh -N -L <local>:<remote-host>:<remote-port> user@bastion` command, and have them run it in a separate terminal.
   - The **read-only database user** and **password**.
   - The default **database** name (ClickHouse) — optional for MySQL.
3. **Write `db_conn_locked/config/db.ini`** from the `.example` template using those values.
   `host` must be `localhost`; `port` must be the **local** tunnel port — confirm this with
   the user, it is the most common setup mistake.
4. **Verify** the tunnel is up (`nc -zv localhost <port>`) before declaring success.
5. **Confirm `db.ini` is not tracked** by git (`git status` should not list it).
6. **Register the MCP server** in the user's client config with absolute paths to their
   `.venv/bin/python` and `server.py`.

Never write credentials to anywhere except `db.ini`. Never commit `db.ini`.

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
| `get_history(connection, table_name?)` | Past query execution history, optionally filtered by table. |
| `explain(sql, connection)` | Run `EXPLAIN`; returns plan + cost estimates. |

All tools are **read-only**. `connection` names a `db.ini` section; `database` (in
`list_tables`) names a table namespace *inside* that server.

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
db_conn_locked/config/db.ini          # your credentials (gitignored, never commit)
db_conn_locked/config/db.ini.example  # template (committed)
database/                             # caches + query history (gitignored)
server.py                             # MCP server entrypoint
db_query.py                           # query logic
```

---

## Troubleshooting

**"Connection refused" / "timed out" when the agent queries anything**
→ Your SSH tunnel is down or pointing at the wrong port. Re-run the `ssh -N -L ...` command
from [Step 2](#step-2-reach-your-databases-ssh-tunnel), and check `nc -zv localhost <port>`.

**"Unknown connection" / wrong connection used**
→ The `connection` name must match a `[section]` in `db.ini`. Run
`list_connections()` to see what's configured. Check `SQL_BUDDY_DEFAULT_CONNECTION`.

**"Write operation blocked by db_conn_locked"**
→ This is **working as intended.** SQL Buddy rejected a mutating statement. Use a
different tool if you genuinely need to change data — SQL Buddy cannot do it.

**Agent keeps querying the wrong database for a table**
→ Have it call `find_table("table_name")` first — it searches every connection and reports
where the table actually lives.

**`db.ini` shows up in `git status` as a new/changed file**
→ Stop. Do **not** commit it. It should be gitignored. If it's already tracked, remove it
from git history (it contains live credentials):
```bash
git rm --cached db_conn_locked/config/db.ini
```

---

## Security model

- **Read-only enforced in code**, before any SQL reaches the network. Write keywords
  (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, …) are rejected by a guard
  layer wrapping every connection.
- **Credentials stay local** — in `db.ini` on your machine, never sent to any third party.
- **`LIMIT` is always applied** to `SELECT`/`WITH` (default 10, hard cap 2000) to prevent
  accidental huge scans.
- **`db.ini` is gitignored.** Verify with `git status` before every push.
