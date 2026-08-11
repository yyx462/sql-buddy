# Detailed setup

The [root `README.md`](../README.md) is the short path (clone → `init` → `mcp add`).
This page is the deep companion: full command reference, the SSH-tunnel deep dive,
every `db.ini` field, connecting non-Claude/Trae clients, agent-install rules, and
updating. Nothing here changes product behavior — it's the same steps, spelled out.

---

## Command reference

`sql-buddy` is a single dispatcher behind the console script. Bare `sql-buddy`
(or `python server.py` with no subcommand) starts the MCP server — that's what
your AI client invokes, so configs hold no fragile absolute `.venv/bin/python`
path.

Run any command via `uv run sql-buddy …` (or `.venv/bin/sql-buddy …`).

| Command | What it does |
|---------|--------------|
| `sql-buddy version` | Print the installed version. |
| `sql-buddy doctor` | Validate the install: venv, `db.ini`, MCP client config. Add `--connect` to ping each Connection (`SELECT 1`). |
| `sql-buddy init` | Interactive setup wizard (writes `db.ini`). Agents/CI: `--non-interactive --json-stdin` or `--json-file`. |
| `sql-buddy mcp print` | Print the MCP client config block (paths filled in). `--python` for a `sys.executable`-based config. `--client claude\|trae`. |
| `sql-buddy mcp add` | Write that block into a client config. `--client claude` (default) or `trae`; `--scope project\|user`. |
| `sql-buddy update-check` | Compare your checkout to the git remote; tells you when to `git pull`. |

> Prefer plain pip? `python3.11 -m venv .venv && source .venv/bin/activate && pip install -e .`,
> then `.venv/bin/sql-buddy doctor` (Windows: `.venv\Scripts\sql-buddy.exe doctor`).

---

## How it works

```
Your AI agent  ──MCP──►  SQL Buddy (runs on your laptop)  ──►  your databases
                          • opens its own SSH tunnel            (behind a bastion)
                          • read-only guard · auto LIMIT · schema lookup
```

SQL Buddy runs **locally** (a small Python program, not a cloud service). It
**opens the SSH tunnel itself** from a bastion + key + per-DB port mapping in
`db.ini` — no separate `ssh -N` terminal, and your credentials never leave your
machine. It's **read-only by design**: a guard layer rejects every write keyword
before it touches the network.

---

## Step 1: Install SQL Buddy

```bash
git clone <this-repo-url> sql-buddy
cd sql-buddy
uv run server.py          # creates an isolated .venv + installs deps (Ctrl+C to stop)
```

If it prints no errors, the install worked. `uv run` provisions an isolated
`.venv` and installs deps for you.

---

## Step 2: Reach your databases (SSH tunnel — managed by SQL Buddy)

Your databases sit behind a firewall, reachable only from inside the network.
SQL Buddy opens an **SSH tunnel** itself: it maps a **port on your laptop** to
the **database port**, through a **bastion** (jump-host) you can SSH into. You
give it the bastion + key + per-DB port mapping in `db.ini`; no separate
`ssh -N` terminal. The tunnel starts lazily on first query, auto-restarts if it
drops, and tears down on exit — one bastion → one tunnel, even for many databases.

> **Confirm ssh works first** (recommended) — test the exact leg SQL Buddy will
> run, before writing `db.ini`:
> ```bash
> ssh -N -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
>     -p 22222 -i ~/.ssh/id_ed25519 \
>     -L 127.0.0.1:10021:10.0.0.21:9000 \
>     zhang.san@192.0.2.10
> ```
> Hangs at "port 22" despite `-p 22222` → wrong port/firewall. `Permission
> denied (publickey)` → wrong user, unregistered key, or key too open
> (`chmod 600`). Ctrl+C when it works and let SQL Buddy manage it.

You describe the tunnel in `db.ini` (next step) with an **`[ssh:<name>]`** section
for the bastion, and a `tunnel = ssh:<name>` line on each connection giving its
`remote_host`/`remote_port` (DB as seen **from the bastion**) and `local_port`
(the port SQL Buddy binds on localhost). Remote port = the real DB port
(ClickHouse **9000**, MySQL **3306**).

> First connection only: `StrictHostKeyChecking=accept-new` auto-trusts the
> bastion's host key. To vet it yourself, `ssh you@bastion -p <port>` once first.

---

## Step 3: Add your credentials (`db.ini`)

Easiest — run the interactive wizard (asks Auto vs **External** mode, then writes
`db.ini`):

```bash
.venv/bin/python init.py
```

Or by hand:

```bash
cp connections/config/db.ini.example connections/config/db.ini
$EDITOR connections/config/db.ini
```

Each database is one **section**; the `[bracketed]` name becomes the
**connection name** your agent uses (e.g. `ck`, `mysql`, `prod-read`).

```ini
# db.ini  -  NEVER commit this file. It is gitignored for a reason.

# --- bastion ---
[ssh:prod]
host = bastion.example.com
port = 22
user = your_ssh_user
key  = ~/.ssh/id_ed25519          # path to your private key (~ expanded)

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

# --- a direct (non-tunneled) connection also works ---
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
| `database` | connection | ClickHouse | The default database to query (the namespace tables live in). |
| `host` | `[ssh:*]` | yes | The bastion/jump-host you SSH into. |
| `port` | `[ssh:*]` | no | SSH port (default 22). |
| `user` | `[ssh:*]` | yes | SSH login user on the bastion. |
| `key` | `[ssh:*]` | yes | Path to your private key file (`~` is expanded). Key-based auth only. |
| `server_alive_interval` | `[ssh:*]` | no | SSH keepalive ping seconds (default 30). |
| `server_alive_count_max` | `[ssh:*]` | no | Drop the tunnel after this many missed pings (default 3). |

> **Naming:** `my_db` and `my-db` are the same — underscores and dashes are
> treated interchangeably.

🔒 **`db.ini` is gitignored and must never be committed.** Double-check with
`git status` before pushing.

---

## Step 4: Test it works

```bash
uv run sql-buddy doctor             # offline: venv + db.ini shape + MCP client config
uv run sql-buddy doctor --connect   # also opens each tunnel and runs SELECT 1
```

`--connect` opens the tunnel on first use (a few seconds per connection). If
every line reads `OK`, you're ready to wire it into your AI agent.

```bash
export SQL_BUDDY_DEFAULT_CONNECTION=ck   # optional: default when agent omits connection
```

---

## Step 5: Connect your AI agent (MCP config)

Let sql-buddy write the config for you:

```bash
uv run sql-buddy mcp add                      # Claude Code project → ./.mcp.json
uv run sql-buddy mcp add --scope user         # Claude Code user    → ~/.claude.json
uv run sql-buddy mcp add --client trae        # Trae project        → ./.trae/mcp.json
uv run sql-buddy mcp add --client trae --scope user   # Trae user-global
```

Restart your client. Preview first with `uv run sql-buddy mcp print` (add
`--client trae` for Trae-shaped env/timeouts). It produces (paths filled in):

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

> No `uv` on the machine that runs your MCP client? Use the Python form:
> `sql-buddy mcp print --python` sets `command` to the running interpreter
> (`sys.executable`) — the most robust absolute path.

Ask your agent *"list the tables in the `ck` connection"* — if it answers,
you're live. 🎉

### Other MCP clients (Cursor, Continue, Workbuddy, …)

`mcp add` ships built-in templates for Claude and Trae only. For any other
MCP-compatible client, hand its agent this prompt — it uses `mcp print` for the
exact launch block and writes the config into whatever location that client uses:

```
You have a read-only database MCP server called sql-buddy in this checkout,
exposed as a CLI. Set it up so you can query my databases through it. Use the
sql-buddy CLI as the source of truth — don't guess paths or config. Run every
sql-buddy command below prefixed with:  uv run   (from the sql-buddy checkout dir).

1. Confirm:  uv run sql-buddy version
2. Get the canonical launch block — use it VERBATIM, including command/args/env:
   uv run sql-buddy mcp print
3. Register sql-buddy in YOUR OWN MCP client config. You know best where your client
   keeps its server list and what shape it expects (.mcp.json, ~/.<yourclient>/mcp.json,
   settings). Add a server entry named "sql-buddy" using the command/args/env from
   step 2. (If your client is claude or trae, you can instead run:
   uv run sql-buddy mcp add --client <claude|trae> [--scope user].)
4. Ensure credentials exist:  uv run sql-buddy doctor
   If no db.ini, run  uv run sql-buddy init  (I'll answer the prompts) or I'll give
   you details to write db.ini at the path "doctor" names.
5. Validate:  uv run sql-buddy doctor --connect   (every connection should read OK)
6. Report back which file you wrote and the exact command/args you registered.
   I'll restart you so the server loads.
7. You hand-wrote config for a client without a built-in mcp-add template — help
   the project add one. Prepare a short report: client name + version, your OS,
   and the exact JSON you wrote (blank out any secrets). If `gh` is available,
   file it directly:
     gh issue create -R yyx462/sql-buddy -t "MCP client template: <client>" -b "<report>"
   Otherwise print the report and ask me to paste it at
   https://github.com/yyx462/sql-buddy/issues/new

Notes: sql-buddy is read-only (SELECT only). Don't invent paths — use exactly
what "uv run sql-buddy mcp print" emits.
```

#### Worked example — WorkBuddy on Windows (verified, `0.3.1`)

Config at `C:\Users\<you>\.workbuddy\mcp.json`; `db.ini` at the path `doctor`
names on Windows, `%LOCALAPPDATA%\sql-buddy\sql-buddy\db.ini`. The agent ran
`mcp print`, which on Windows resolves `uvx` to an absolute path and emits the
UTF-8 vars the console needs — then copied the block verbatim:

```json
{
  "mcpServers": {
    "sql-buddy": {
      "command": "C:\\Users\\<you>\\.local\\bin\\uvx.EXE",
      "args": ["--from", "sqlbuddy-nwi", "sql-buddy"],
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

> `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` keep Chinese / non-ASCII output from
> turning into `???` on the Windows console. `mcp print` adds them automatically
> on Windows (and only there); Linux/macOS omit them (already UTF-8).

**Set up a client not listed here?** Please [file an issue](https://github.com/yyx462/sql-buddy/issues/new)
with the client name, your OS, and the JSON config that worked — we'll fold the
common ones into `mcp add` as built-in templates.

---

## For AI agents: installing on a user's machine

If you (the agent) are setting SQL Buddy up for a user, follow the steps above —
and observe these agent-specific rules:

- **Never guess a value.** Ask the user, one question at a time, in plain
  language. Bastion `host`/`port`/`user`, key path, and DB credentials must come
  from them. (**Don't assume ssh port 22** — many orgs use a high port like
  `22222`; assuming 22 hangs silently.)
- **Write `db.ini` the structured way**, not by hand-formatting INI:
  `uv run sql-buddy init --non-interactive --json-stdin` (pipe the spec JSON on
  stdin; schema in `init.py`). It validates and writes for you. **Credentials
  travel on stdin** (or a `0600` temp file via `--json-file` you `rm` after) —
  never leave a password-bearing file in the checkout.
- **Ask for the user's manual ssh command** if they have one — reading
  `host`/`port`/`user`/`key` and the `-L local:remote_host:remote_port` triple
  off it is far less error-prone than asking for each field separately.
- **`remote_host`/`remote_port` are the DB as seen from the bastion**, not the
  laptop. Getting this wrong is the #1 setup failure.
- **Verify with `uv run sql-buddy doctor --connect`** — every line should read
  `OK`. On any `FAIL`, read the error JSON's `hint` and follow it (every tool
  error carries one).
- **Confirm `db.ini` is gitignored** (`git status` must not list it) before any
  commit.

---

## Updating

```bash
uv run sql-buddy update-check     # compares your checkout to the git remote
git pull                          # apply the update
uv run server.py                  # re-syncs deps if they changed
```

`sql-buddy version` prints what you're running. Your `db.ini` is never touched by
an update — it's gitignored and lives outside the tracked code.
