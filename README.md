# SQL Buddy

A read-only **MCP server** that lets your AI agent (Claude Code, and any
MCP-compatible client) safely query your **MySQL** and **ClickHouse** databases.

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

## Quickstart (60 seconds)

Already have [`uv`](https://docs.astral.sh/uv/) and SSH access? The whole install
is one command — `uv run` provisions an isolated `.venv` and installs deps for you,
so there is no separate `venv`/`pip install` step:

```bash
git clone <this-repo-url> sql-buddy && cd sql-buddy
uv run server.py            # installs deps, then starts the server (Ctrl+C to stop)
```

Then configure your Connections and validate everything in one shot:

```bash
uv run sql-buddy init       # interactive wizard → writes connections/config/db.ini
uv run sql-buddy doctor     # green-checks the venv, db.ini, and MCP client config
uv run sql-buddy mcp add    # registers sql-buddy with Claude Code (writes .mcp.json)
```

`uv run sql-buddy <command>` runs from anywhere inside the checkout (from
outside it, use `uv --directory /path/to/sql-buddy run sql-buddy …`, or call
the venv's `sql-buddy` directly). Subcommands: `version`, `doctor [--connect]`,
`mcp print|add`, `update-check`, `init`. **Bare `sql-buddy` (no command) runs the
MCP server** — that's what your AI client invokes, so MCP client configs hold no
fragile absolute path to `.venv/bin/python`.

New here? Walk through the full [Setup](#setup) below.

---

## Prerequisites

You will need:

1. **Python 3.11+** - `python3 --version`
2. **[uv](https://docs.astral.sh/uv/)** (fast Python installer) - or plain `pip` works too.
3. **SSH access** to a bastion/jump-host that can reach your databases:
   - the bastion `user@host` and ssh `port` (ask your ops team — **don't assume 22**;
     many orgs run sshd on a non-standard port like `22222`),
   - a **private key file** on your machine (issued by your admin — key-based auth only),
   - for each database, the DB `host:port` **as seen from the bastion** (private internal
     IPs like `10.0.0.x`, not your laptop's view).
4. **Read-only database credentials** - ask your DBA for a user that can `SELECT` but not write.
5. An **MCP-compatible client** to connect it to (e.g. Claude Code).

---

## Setup

### Step 1: Install SQL Buddy

```bash
git clone <this-repo-url> sql-buddy
cd sql-buddy

# `uv run` creates an isolated .venv and installs dependencies for you.
# Smoke-test the server (Ctrl+C to stop):
uv run server.py
```

If it prints no errors, the install worked. Stop it for now - we configure databases next.

This also gives you the `sql-buddy` command (run via `uv run sql-buddy …` or
`.venv/bin/sql-buddy …` once the env exists):

| Command | What it does |
|---------|--------------|
| `sql-buddy version` | Print the installed version. |
| `sql-buddy doctor` | Validate the install: venv, `db.ini`, and MCP client config. Add `--connect` to also ping each Connection (`SELECT 1`). |
| `sql-buddy init` | Interactive setup wizard (writes `db.ini`). Accepts `--non-interactive --json` for agents/CI. |
| `sql-buddy mcp print` | Print the MCP client config block (paths filled in). `--python` for a `sys.executable`-based config. |
| `sql-buddy mcp add` | Write that block straight into Claude Code's config (`.mcp.json` or `~/.claude.json`). |
| `sql-buddy update-check` | Compare your checkout to the git remote; tells you when to `git pull`. |

> Prefer plain pip, or need the explicit venv?
> ```bash
> python3.11 -m venv .venv && source .venv/bin/activate && pip install -e .
> # or with uv:  uv venv .venv --python 3.11 && uv pip install -e .
> .venv/bin/sql-buddy doctor   # then use .venv/bin/sql-buddy / .venv/bin/python server.py
> ```

---

### Step 2: Reach your databases (SSH tunnel - managed by SQL Buddy)

Most databases are **not exposed to the public internet** - they sit behind a firewall
and are only reachable from inside the network. SQL Buddy reaches them by opening an
**SSH tunnel** itself: a secure pipe that maps a **port on your laptop** to the
**database port on the remote server**, through a **bastion** (a.k.a. jump-host) you
can SSH into.

> **What is a "bastion"?** A bastion is a single public-facing server your company
> exposes so you can SSH *into* the internal network. You log in to the bastion from
> the internet; the bastion then has line-of-sight to private internal hosts (your
> databases, internal services, etc.). Think of it as the only door into the walled
> garden - everything else is firewalled off.
>
> **Concrete example (shapes only — replace with your own values):**
>
> - bastion host: `192.0.2.10` (a public IP your ops team publishes)
> - bastion ssh port: `22222` ← **not 22!** Many orgs run sshd on a high, non-standard
>   port to dodge drive-by scanners. Always confirm with ops; assuming 22 will hang.
> - ssh user: `zhang.san` (companies usually issue one in `first.last` form,
>   tied to your identity)
> - private key: `~/.ssh/id_ed25519` (issued by your admin when they granted access;
>   the *public* half is registered to your username on the bastion)
>
> Then, *inside* the network the bastion can see, your databases live at private IPs:
>
> | DB label   | DBMS       | Bastion's view (`remote_host:remote_port`) | Pick a `local_port` on your Mac |
> |------------|------------|---------------------------------------------|----------------------------------|
> | `ck`       | ClickHouse | `10.0.0.21:9000`                         | `10021`                          |
> | `mysql`    | MySQL      | `10.0.0.11:3306`                         | `10011`                          |
>
> The **local_port** is any free port between 1024 and 65535 on *your* laptop - pick
> numbers you can remember and that nothing else on your machine is using. SQL Buddy
> binds `127.0.0.1:local_port` and the DB driver connects there - it never knows SSH
> is in the middle.

You do **not** run a separate `ssh -N` terminal. SQL Buddy starts the tunnel lazily on
the first query that needs it, keeps it alive for the life of the server, restarts it
if it drops, and tears it down when the server exits. One bastion -> one tunnel, even
with many databases behind it.

> **Manual smoke test first** (recommended): confirm your bastion + key + port work
> before writing `db.ini`. Equivalent to what SQL Buddy will run for you:
>
> ```bash
> ssh -N -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
>     -p 22222 -i ~/.ssh/id_ed25519 \
>     -L 127.0.0.1:10021:10.0.0.21:9000 \
>     zhang.san@192.0.2.10
> ```
>
> If this hangs at "Connecting to … port 22" *but you specified `-p 22222`* → sshd
> isn't reachable on that port from your network (firewall, wrong port, ops changed
> it). If it says `Permission denied (publickey)` → the key isn't accepted (wrong
> user, key not registered, key file too open — `chmod 600 ~/.ssh/id_ed25519`).
> If it works → Ctrl+C and let SQL Buddy manage it instead.

You describe the tunnel in `db.ini` (next step) with:

- an **`[ssh:<name>]`** section for the bastion (host, port, user, key file), and
- a **`tunnel = ssh:<name>`** line on each database connection, plus its
  `remote_host` / `remote_port` (the DB as seen from the bastion) and `local_port`
  (the port SQL Buddy binds on localhost).

The remote port must match the real database port (ClickHouse default **9000**,
MySQL default **3306**).

> **First connection only:** SQL Buddy uses `StrictHostKeyChecking=accept-new`, so the
> bastion's host key is auto-trusted on first run. If you prefer to vet it manually,
> `ssh you@bastion -p <port>` once first to add it to `~/.ssh/known_hosts`.

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
cp connections/config/db.ini.example connections/config/db.ini
$EDITOR connections/config/db.ini
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

One command validates the install and (optionally) pings each Connection:

```bash
uv run sql-buddy doctor             # offline: venv + db.ini shape + MCP client config
uv run sql-buddy doctor --connect   # also opens each tunnel and runs SELECT 1
```

`--connect` opens the SSH tunnel on first use (it can take a few seconds per
connection, and longer if a bastion is unreachable). If every line is `OK`, the
tunnel came up and you're ready to wire it into your AI agent.

```bash
# (optional) Set which connection is used when the agent doesn't specify one
export SQL_BUDDY_DEFAULT_CONNECTION=ck
```

---

### Step 5: Connect your AI agent (MCP config)

Easiest: let sql-buddy write the config for you.

```bash
uv run sql-buddy mcp add                 # project scope → writes ./.mcp.json (Claude Code)
uv run sql-buddy mcp add --scope user    # user scope   → writes ~/.claude.json
```

Restart your client and you're done. Preview the exact block first with
`uv run sql-buddy mcp print`. It produces this (paths filled in for you) — the
**`uv --directory … run`** form auto-provisions the venv and carries no absolute
`.venv/bin/python` path to go stale:

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

> No `uv` on the machine that runs your MCP client? Use the Python form instead:
> `sql-buddy mcp print --python` (or `mcp add --python`) — it sets `command` to the
> interpreter running the CLI (`sys.executable`), the most robust absolute path.

Ask your agent something like *"list the tables in the `ck` connection"* - if it
can answer, you're live. 🎉

---

## Updating

Check whether a newer version is available, then pull:

```bash
uv run sql-buddy update-check     # compares your checkout to the git remote
git pull                          # apply the update
uv run server.py                  # `uv run` re-syncs deps if they changed
```

`sql-buddy version` prints what you're running. If you installed via a vendored
bundle instead of git, re-download the latest release to update. Your `db.ini` is
never touched by an update — it's gitignored and lives outside the tracked code.

---

## 👉 For AI agents: setting this up for a user

If you (the agent) are installing SQL Buddy on a user's machine, **do not guess** any
value below. Ask the user explicitly, one at a time, in plain language.

> **Drive the setup straight after `git clone`** — do not stall at "what next?". The
> whole point is for the user to be querying databases 5 minutes after cloning. Once
> step 1's `uv run server.py` returns, immediately proceed to steps 2-5 to collect
> values and write `db.ini`. The only blocker between you and a finished install is
> the user-supplied values in steps 2-4 — get to those questions now.

> Prefer the structured path to `db.ini`: collect the values below, then write
> them with **`uv run sql-buddy init --non-interactive --json-stdin`** (pipe the
> spec JSON on stdin) or **`--json-file <path>`** (schema in `init.py`). It
> validates and writes `db.ini` for you — no hand-formatting INI. Feed credentials
> via **stdin or a file**, not inline `--json`: argv is visible in process
> listings and shell/agent history. You can also write `db.ini` directly with
> your file-editing tool if you prefer.

1. **The repository** - clone it, then `uv run server.py` (Ctrl+C once it starts —
   that one command created the venv and installed deps). As soon as it returns,
   **move to step 2 — do not stop to ask "what next?"**.
2. **Ask the user which mode** they want:
   - **Auto** - sql-buddy opens the SSH tunnel itself. Continue to step 3.
   - **External** - the user already runs a tunnel/connection (e.g. `ssh -N` on Windows).
     Skip to step 4 (External branch).
3. **Auto mode - SSH bastion** they reach their databases through. Many users have
   never heard the word "bastion" — explain it in one line ("the public server your
   company lets you SSH *into*, from which you can reach private internal hosts").
   Collect:

   | Field        | What it is                                                | Where the user finds it                        |
   |--------------|-----------------------------------------------------------|------------------------------------------------|
   | `host`       | Public IP / hostname of the bastion                       | From their ops / infra team                     |
   | `port`       | SSH port the bastion listens on                           | From ops. **Do not assume 22** — many orgs run sshd on a high port like `22222`; assuming 22 will hang silently on `Connecting to host port 22` |
   | `user`       | SSH login user, usually `first.last` form (e.g. `zhang.san`) | The username IT registered for them        |
   | `key`        | Absolute path to their **private** key file on this Mac   | Issued by their admin when access was granted. The matching *public* key is registered on the bastion under their username, so the private key + username are tied together |
   | keepalive    | (optional) `server_alive_interval` / `server_alive_count_max` | Defaults 30s / 3 are fine — only set if ops says so          |

   Ask the user to **paste the equivalent ssh command they would run manually**, if
   they have one — that's the single most reliable way to extract host/port/user/key
   without transposition errors:

   ```
   ssh -N -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
       -p 22222 -i /Users/<them>/.ssh/id_ed25519 \
       -L 127.0.0.1:10021:10.0.0.21:9000 \
       zhang.san@192.0.2.10
   ```

   From that line you can read off `host=192.0.2.10`, `port=22222`,
   `user=zhang.san`, `key=/Users/<them>/.ssh/id_ed25519`. The `-L` line also gives
   you one database's `local_port:remote_host:remote_port` triple immediately.

4. **For each database** the user wants to query, collect (in plain language, then
   translate to `db.ini` keys):

   | `db.ini` key     | Plain-language question                                                        | Example value            |
   |------------------|--------------------------------------------------------------------------------|--------------------------|
   | (section name)   | "What short label do you want for this connection?" (use `_` or `-`)           | `ck`, `mysql`, `prod`    |
   | `type`           | "Is it ClickHouse or MySQL?"                                                   | `clickhouse` / `mysql`   |
   | `remote_host`    | "From *on the bastion*, what IP/hostname is the DB at?" (NOT their laptop's)  | `10.0.0.21`           |
   | `remote_port`    | "What port does the DB listen on there?" (CH default 9000, MySQL 3306)       | `9000`                   |
   | `local_port`     | "Pick a free port on your Mac between 1024-65535" (offer to pick one for them)| `10021`                   |
   | `user`           | "The read-only DB username (ask your DBA — must be SELECT-only)"             | `api_asia`                |
   | `password`       | "That user's password"                                                         | *(don't echo it back)*   |
   | `database`       | "Default database name?" (required for ClickHouse, optional for MySQL)        | `default` / `myapp`      |

   - **Auto mode:** needs all of `remote_host` / `remote_port` / `local_port`. No
     `host` / `port` keys on this connection (those are direct-only).
   - **External mode:** the user already runs the tunnel — collect `host` (usually
     `localhost`) and `port` (the local tunnel port). No `tunnel`/`remote_*`/`local_port`
     keys.
5. **Write `connections/config/db.ini`** (see `db.ini.example` for the shape):
   - Auto: one `[ssh:*]` section for the bastion + each connection with
     `tunnel = ssh:*` and its `remote_host`/`remote_port`/`local_port` (no `host`/`port`).
   - External: each connection with `host`/`port`/`user`/`password` (and `database` for
     ClickHouse). No `[ssh:*]` block.
6. **Verify** with `uv run sql-buddy doctor --connect`. Expect every line to read
   `OK` (it runs `SELECT 1` over each connection and opens the tunnel on first use).
   Any `FAIL` line is self-explanatory; read the tool-error `hint` and follow it (in
   External mode a transport error usually means the user's tunnel is down).
   **Common Auto-mode failures and fixes:**
   - hangs at "Connecting to host port 22" but you specified another `-p` → sshd not
     reachable on that port. Confirm the port with ops.
   - `Permission denied (publickey)` → wrong user, key not registered on bastion, or
     key file too permissive (`chmod 600 <key>` fixes the third one).
   - tunnel comes up but DB call times out → wrong `remote_host`/`remote_port`
     (those are the DB *as seen from the bastion*, not from the laptop).
7. **Confirm `db.ini` is not tracked** by git (`git status` should not list it).
8. **Register the MCP server** with one command — it writes the `uv --directory … run`
   config straight into the client (no absolute `.venv/bin/python` path to verify):
   ```bash
   uv run sql-buddy mcp add --scope user     # ~/.claude.json (user-global)
   # or, for a single project:  uv run sql-buddy mcp add   # → ./.mcp.json
   uv run sql-buddy mcp print                 # preview the exact block first if you like
   ```
   Then tell the user to restart their MCP client.

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
.mcp.json                             # project-scope MCP client config (uv run server.py)
connections/config/db.ini          # your credentials + ssh blocks (gitignored, never commit)
connections/config/db.ini.example  # template (committed)
database/                             # caches + query history (gitignored)
server.py                             # MCP server entrypoint (bare run = serve over stdio)
cli.py                                # `sql-buddy` CLI: doctor / mcp / version / update-check
db_query.py                           # query logic
db_security.py                        # secret-redaction + .env loader (installed at startup)
tunnel.py                             # self-managed SSH tunnels (one ssh -N per [ssh:*] block)
init.py                               # setup wizard — interactive, or --non-interactive --json
_version.py                           # single source of truth for the version
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

**"Write operation blocked by sql-buddy's read-only guard"**
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
git rm --cached connections/config/db.ini
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
