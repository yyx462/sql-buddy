# Security Policy

## Reporting a Vulnerability

SQL Buddy enforces a **read-only invariant** — no write SQL may ever reach a
real database. If you find a way to bypass the Locked layer, leak credentials,
or otherwise break that guarantee, **please report it privately** rather than
opening a public issue.

**Preferred channel — GitHub Private Vulnerability Reporting:**

Go to the **Security** tab → **Report a vulnerability**. This opens a private
advisory visible only to the maintainers. Do **not** open a public GitHub
issue or PR for security-sensitive bugs.

Please include:

- A description of the issue and its impact (read-only bypass, secret
  exposure, crash, …).
- The code path or input that triggers it, with a minimal repro.
- The `sql-buddy --version` or git commit you tested against.

## Scope

In scope:

- Any path that sends a write keyword (`INSERT`, `UPDATE`, `DELETE`, `DROP`,
  `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, …) to a live database, or that
  circumvents the Locked layer.
- Exposure of credentials, SSH keys, or `db.ini` contents via logs, stdout,
  error messages, or MCP responses.
- Resource leaks or crashes reachable from normal tool input.

Out of scope:

- Bugs that require an attacker to already control the `db.ini` file or the
  machine running the server (the trust boundary is the MCP client; everything
  behind `db.ini` is trusted).
- Theoretical issues without a concrete path through the public tool surface.

## Response

Acknowledgement within a few days. Fixes ship as a patch release on PyPI
(`sqlbuddy-nwi`) and a fresh tag on GitHub. Coordinated disclosure once the
fix is published.
