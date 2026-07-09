"""Read-only SQL guard.

The enforcement layer that makes every connection read-only by construction:
write/mutating keywords are rejected here, before any SQL reaches the network.
"""

import re

# SQL verbs that mutate state. Matched as whole words (case-insensitive).
WRITE_KEYWORDS = [
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER',
    'TRUNCATE', 'REPLACE', 'MERGE', 'CALL', 'EXEC', 'EXECUTE',
    'GRANT', 'REVOKE', 'LOCK', 'UNLOCK', 'SET GLOBAL',
    'KILL', 'LOAD DATA', 'START TRANSACTION', 'BEGIN'
]

# Verbs a read-only query may begin with. Anything else is treated as a write.
READ_ONLY_KEYWORDS = [
    'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN',
    'USE', 'WITH', 'EXISTS', 'CHECK', 'HELP'
]


def _is_write_operation(sql: str) -> bool:
    sql_upper = sql.strip().upper()
    for keyword in READ_ONLY_KEYWORDS:
        if sql_upper.startswith(keyword):
            return False
    for keyword in WRITE_KEYWORDS:
        pattern = r'\b' + keyword + r'\b'
        if re.search(pattern, sql_upper):
            if keyword == 'CREATE' and 'SHOW' in sql_upper:
                continue
            return True
    return False


def _filter_sql(sql: str) -> str:
    """Pass through `sql` unchanged, or raise PermissionError if it mutates."""
    if _is_write_operation(sql):
        raise PermissionError(
            "Write operation blocked by sql-buddy's read-only guard. "
            f"Only READ queries are allowed. Blocked SQL: {sql[:100]}..."
        )
    return sql
