# Ubiquitous Language

SQL Buddy: a read-only database access server exposed over the MCP protocol for an AI agent. The agent issues SQL through a small set of tools; every path is forced read-only and results are cached.

## Connections & namespaces

| Term                    | Definition                                                                      | Aliases to avoid                |
| ----------------------- | ------------------------------------------------------------------------------- | ------------------------------- |
| **Connection**          | A configured, named DB endpoint; one `db.ini` section, exposed as the `connection` param (e.g. `ck`) | database, db, db_name (legacy) |
| **DBMS database**       | A namespace *inside* a server that groups tables (`SHOW TABLES FROM` target)    | schema, database                |
| **Table**               | A table within a **DBMS database**, reached through a **Connection**            | relation, tbl                   |
| **Connection type**     | Whether a **Connection** speaks ClickHouse or MySQL; inferred from section name | engine, flavor                  |

## Query execution

| Term                | Definition                                                                         | Aliases to avoid                  |
| ------------------- | ---------------------------------------------------------------------------------- | --------------------------------- |
| **Query**           | A single read-only SQL statement the agent asks the server to run                  | sql, statement, request           |
| **Query hash**      | An 8-char md5 of a **Query**, used as its history key and optimization identity    | hash, id                          |
| **Query history**   | The persisted log of executed **Queries**, keyed by **Query hash**                 | cache, log                        |
| **LIMIT**           | A row cap auto-added to SELECT/WITH **Queries** (default 10, clamped to max 100)   | limit clause, row limit           |
| **Slow query**      | A **Query** whose execution exceeded the 30-second threshold                       | slow, timeout                     |
| **Optimized query** | A rewritten **Query** the server records as a faster equivalent of a **Slow query**| better_sql, suggestion            |
| **Explain**         | Running `EXPLAIN` on a **Query** to return its plan and cost estimates             | plan, cost analysis               |

## Introspection

| Term           | Definition                                                                              | Aliases to avoid        |
| -------------- | --------------------------------------------------------------------------------------- | ----------------------- |
| **Schema**     | The `CREATE TABLE` text of a **Table**, parsed into columns, engine, partition, etc.    | create statement, ddl   |
| **Table info** | Size/metadata of a **Table**: row count, indexes (MySQL) or partitions (ClickHouse)     | stats, metadata         |
| **Resource**   | An MCP resource exposing one configured **Table**'s **Schema**, generated from env var | table resource, uri     |

## Safety layer

| Term                  | Definition                                                                             | Aliases to avoid     |
| --------------------- | -------------------------------------------------------------------------------------- | -------------------- |
| **Read-only**         | The invariant that no mutating SQL ever reaches a real database                        | safe, no-writes      |
| **Locked**            | The wrapper layer that enforces **Read-only** by blocking **Write keywords**           | filter, guard        |
| **Write keyword**     | A SQL verb (`INSERT`, `DROP`, `ALTER`, …) the **Locked** layer rejects                 | mutation, dml/ddl    |
| **Read-only keyword** | A SQL verb (`SELECT`, `SHOW`, `DESCRIBE`, …) a **Query** must begin with               | allowed verb         |

## Relationships

- A **Connection** points at one server and gives access to many **DBMS databases**, each holding many **Tables**.
- A **Table** belongs to exactly one **DBMS database** and is reached through exactly one **Connection**.
- A **Query** runs against one **Connection** and reads one or more **Tables**; it produces exactly one **Query hash**.
- A **Slow query** is linked (via union-find) to at most one **Optimized query**, recorded under its own **Query hash**.
- A **Schema** describes one **Table** and is cached per **Connection**.
- A **Resource** exposes one **Table**'s **Schema** and is only generated for **Tables** listed in the config env var.

## Example dialogue

> **Agent:** "I'll run this `SELECT` on `ck`. What does the `connection` param mean?"
>
> **Operator:** "`ck` is a **Connection** — a configured endpoint. Don't confuse it with the **DBMS database** inside it; that's the namespace the `FROM` clause points at."
>
> **Agent:** "So when I call `list_tables(connection='ck', database='sales')`, `ck` is the **Connection** and `sales` is a **DBMS database**?"
>
> **Operator:** "Exactly. Two distinct concepts, no longer sharing a word in the API."
>
> **Agent:** "What if I try an `UPDATE`?"
>
> **Operator:** "Blocked. The **Locked** layer sees a **Write keyword** and refuses before it ever hits the server. Everything stays **Read-only**. And remember every `SELECT` gets a **LIMIT** — 10 rows unless you say more, capped at 100."
>
> **Agent:** "That last `SELECT` took 40 seconds."
>
> **Operator:** "That's a **Slow query**. The server records an **Optimized query** and links it to the original **Query hash**, so next time it can suggest the faster version."

## Resolved ambiguities

All five flagged conflicts are now resolved in the code.

- **"database"** — split into **Connection** (the `connection` MCP param, a `db.ini` section) and **DBMS database** (the `database` param in `list_tables`, a table namespace). The old `db_name` param and the `list_databases` tool are gone. Set the default with `SQL_BUDDY_DEFAULT_CONNECTION`.
- **"schema"** — the `get_schema` tool is renamed to `get_table_definition`. "Schema" now means exactly one thing: the `CREATE TABLE` of a single **Table** (and the parsed result of that statement). MySQL's "schema" = a **DBMS database**; the codebase never uses "schema" for that concept.
- **"query"** — unchanged in code; treated as a doc convention. The `query` tool is the agent-issued read statement; `explain`, `get_table_definition`, and `list_tables` send internal SQL. Speak the **Query** when you mean the agent's input.
- **section-name type discriminator** — each `db.ini` section now declares `type = clickhouse|mysql` explicitly. The prefix-based fallback (`ck*` -> clickhouse, `db*`/`mysql*` -> mysql) still works for older configs, so existing sections keep loading. Renaming a section no longer silently swaps the driver.
- **"history" vs "cache"** — the on-disk layout is now `query_history/` (audit log of executed **Queries**) alongside `db_info_cache/` and `db_schema_cache/` (freshness-bounded, 3 days). Start-fresh: any pre-existing `query_cache/` directory is left on disk but ignored.
