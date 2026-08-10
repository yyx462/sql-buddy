"""SQL Buddy - Database query logic for the MCP server."""

import hashlib
import json
import logging
import os
import re
import sys
import time
import difflib
from datetime import datetime, timedelta
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db_security as _security

from connections import LockedDBManager as DBManager, connection_type
from connections.config_util import config_path, cache_dir_path


def clean_db_error(msg: str) -> str:
    """Strip a DBMS exception's embedded stack trace, keeping the actionable message.

    ClickHouse embeds a C++ server trace directly in the exception text, e.g.:
        Code: 47. DB::Exception: Missing columns: 'x' ... required columns: 'x'. Stack trace:

        0. DB::Exception::Exception(...) @ 0x... in /usr/bin/clickhouse
        1. ...
    Those frames are internal server symbols (hex addresses in /usr/bin/clickhouse)
    that never reference the caller's SQL, column, or table; the message before
    'Stack trace:' already names the offending column/table/query, so we keep it
    and drop the trace. No-op for errors with no embedded trace (MySQL/pymysql,
    config errors, transport errors). Never raises.
    """
    if not msg:
        return msg
    marker = msg.find('Stack trace:')
    if marker == -1:
        return msg
    cleaned = msg[:marker].rstrip()
    return cleaned or msg


# LIMIT enforcement for SELECT/WITH queries
DEFAULT_LIMIT = 10
MAX_LIMIT = 2000

# Cache directories. The root honors $DB_QUERY_HISTORY_DIR, else the per-user
# cache dir when installed (the package tree is read-only under uvx/pip), else
# the checkout-relative `database/` dir. See connections.config_util.cache_dir_path.
ROOT_DIR = cache_dir_path()
QUERY_HISTORY_DIR = os.path.join(ROOT_DIR, 'query_history')
DB_INFO_CACHE_DIR = os.path.join(ROOT_DIR, 'db_info_cache')
DB_SCHEMA_CACHE_DIR = os.path.join(ROOT_DIR, 'db_schema_cache')
DB_TABLE_LIST_CACHE_DIR = os.path.join(ROOT_DIR, 'table_list_cache')

CACHE_EXPIRE_DAYS = 3
MAX_HISTORY_ENTRIES = 1000
CONFIG_FILE = config_path()


def init_dirs():
    for d in [QUERY_HISTORY_DIR, DB_INFO_CACHE_DIR, DB_SCHEMA_CACHE_DIR, DB_TABLE_LIST_CACHE_DIR]:
        os.makedirs(d, exist_ok=True)


def _get_cache_file_path(cache_type: str, connection: str) -> str:
    if cache_type == 'query':
        return os.path.join(QUERY_HISTORY_DIR, f'{connection}_query_history.json')
    elif cache_type == 'info':
        return os.path.join(DB_INFO_CACHE_DIR, f'{connection}_table_info.json')
    elif cache_type == 'schema':
        return os.path.join(DB_SCHEMA_CACHE_DIR, f'{connection}_schema.json')
    elif cache_type == 'tables':
        return os.path.join(DB_TABLE_LIST_CACHE_DIR, f'{connection}_tables.json')
    raise ValueError(f"Unknown cache type: {cache_type}")


def _load_json_safe(file_path: str) -> dict:
    try:
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def _save_json_safe(file_path: str, data: dict):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2, default=str)


def _is_cache_expired(timestamp_str: str, expire_days: int = CACHE_EXPIRE_DAYS) -> bool:
    try:
        timestamp = datetime.fromisoformat(timestamp_str)
        return datetime.now() > timestamp + timedelta(days=expire_days)
    except (ValueError, TypeError):
        return True


def _get_table_name_from_sql(sql: str) -> Optional[str]:
    sql_upper = sql.upper()
    if 'FROM' in sql_upper:
        parts = sql_upper.split('FROM')[1].split()
        if parts:
            tbl = parts[0].strip('();,')
            if '.' in tbl:
                tbl = tbl.split('.')[-1]
            return tbl
    if 'TABLE' in sql_upper:
        parts = sql_upper.split('TABLE')
        if len(parts) > 1:
            return parts[1].strip('();,')
    return None


def _generate_query_hash(sql: str) -> str:
    return hashlib.md5(sql.strip().encode()).hexdigest()[:8]


def _is_query_allowed(sql: str) -> bool:
    sql_trimmed = sql.strip().upper()
    allowed = (
        'SELECT', 'SHOW', 'DESCRIBE', 'DESC', 'EXPLAIN',
        'USE', 'WITH', 'EXISTS',
    )
    return any(sql_trimmed.startswith(p) for p in allowed)


def _result_to_json(client, sql: str) -> str:
    """Execute SQL and return JSON string with columns + rows. Decimals cast to float."""
    if hasattr(client, 'query_dataframe'):
        df = client.query_dataframe(sql)
        if df.empty:
            return json.dumps({"columns": [], "rows": [], "row_count": 0})
        rows = [_sanitize_row(r) for r in df.to_dict(orient='records')]
        return json.dumps({
            "columns": list(df.columns),
            "rows": rows,
            "row_count": len(rows),
        }, default=str)
    else:
        # ClickHouse
        result = client.execute(sql, with_column_types=True)
        if not result or not result[0]:
            return json.dumps({"columns": [], "rows": [], "row_count": 0})
        data, columns = result
        col_names = [c[0] for c in columns]
        rows = [_sanitize_row(dict(zip(col_names, row))) for row in data]
        return json.dumps({
            "columns": col_names,
            "rows": rows,
            "row_count": len(rows),
        }, default=str)


# --- Schema ---

# MergeTree clause keywords that terminate a DDL value expression. All of these
# follow the column list in ClickHouse DDL (PARTITION BY / PRIMARY KEY /
# ORDER BY / SAMPLE BY / SETTINGS / TTL come after `ENGINE = ...`).
_DDL_CLAUSE_STOP = (
    r'ENGINE\b|PARTITION\s+BY\b|PRIMARY\s+KEY\b|ORDER\s+BY\b'
    r'|SAMPLE\s+BY\b|SETTINGS\b|TTL\b'
)


def _extract_ddl_clause(schema: str, clause: str) -> str:
    """Extract the expression following a CREATE TABLE clause keyword.

    `clause` is a regex fragment such as ``PRIMARY\\s+KEY`` or ``ORDER\\s+BY``.
    Captures from just after the keyword up to the next sibling clause keyword
    or end of string. Works for both newline-separated DDL (real
    ``SHOW CREATE TABLE`` output) and single-line DDL (tests), unlike the old
    ``[^\n]+?`` regexes that required a trailing newline terminator and so
    silently returned '' for one-line schemas.
    """
    m = re.search(
        rf'{clause}\s+(.+?)(?=\s+(?:{_DDL_CLAUSE_STOP})|\s*$)',
        schema, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return ''
    return m.group(1).strip().rstrip(',').strip()


def _parse_create_table(schema: str) -> dict:
    schema_clean = schema.replace('\\n', '\n')
    result = {
        'table_name': '', 'engine': '', 'partition': '',
        'primary_key': '', 'order_by': '', 'sampling_key': '',
        'columns': [], 'projections': []
    }

    m = re.search(r'CREATE\s+TABLE\s+([\w.`]+)', schema_clean, re.IGNORECASE)
    if m:
        result['table_name'] = m.group(1).strip('`')

    m = re.search(r'ENGINE\s*=\s*(\w+)', schema_clean, re.IGNORECASE)
    if m:
        result['engine'] = m.group(1)

    # PARTITION BY / PRIMARY KEY / ORDER BY / SAMPLE BY live AFTER the column
    # list in ClickHouse DDL. Restrict the clause search to the tail starting at
    # ENGINE so a MySQL `PRIMARY KEY (...)` declared *inside* the column list is
    # never mistaken for a MergeTree sparse-index key. PRIMARY KEY and ORDER BY
    # are distinct in CH: PRIMARY KEY sets sparse-index granularity (a prefix of
    # ORDER BY), ORDER BY sets the on-disk sort — filtering on an ORDER BY column
    # that is NOT in PRIMARY KEY is a full granule scan.
    engine_match = re.search(r'ENGINE\s*=', schema_clean, re.IGNORECASE)
    clause_tail = schema_clean[engine_match.start():] if engine_match else schema_clean
    result['partition'] = _extract_ddl_clause(clause_tail, r'PARTITION\s+BY')
    result['primary_key'] = _extract_ddl_clause(clause_tail, r'PRIMARY\s+KEY')
    result['order_by'] = _extract_ddl_clause(clause_tail, r'ORDER\s+BY')
    result['sampling_key'] = _extract_ddl_clause(clause_tail, r'SAMPLE\s+BY')

    for match in re.finditer(
        r'`(\w+)`\s+(\w+(?:\([^)]*\))?)\s*(?:DEFAULT\s+[^\s,]+)?\s*(?:COMMENT\s+\'[^\']*\')?',
        schema_clean, re.IGNORECASE
    ):
        col_name, col_type = match.group(1), match.group(2)
        cm = re.search(rf'`{col_name}`[^,]*COMMENT\s+\'([^\']*)\'', schema_clean, re.IGNORECASE)
        result['columns'].append({
            'name': col_name, 'type': col_type,
            'comment': cm.group(1) if cm else ''
        })

    for match in re.finditer(r'PROJECTION\s+(\w+)\s*\((.*?)\)', schema_clean, re.IGNORECASE | re.DOTALL):
        result['projections'].append({
            'name': match.group(1), 'definition': match.group(2).strip()
        })

    return result


# Column names that imply a time column regardless of declared type.
_TIME_COL_NAMES = {'create_time', 'insert_time', 'update_time'}
# Type families that mark a column as time-related.
_TIME_TYPE_RE = re.compile(
    r'^(DateTime64|DateTime|Date32|Date|Timestamp|TIMESTAMP)', re.IGNORECASE
)


def _extract_time_columns(schema: str) -> list:
    """Return time-related column names from a CREATE TABLE statement.

    A column is time-related if its name is create_time/insert_time/update_time
    or its declared type is a Date/DateTime/Timestamp family type. Names are
    returned clean — never a bare backtick or a `` `col` `` token. The old
    heuristic grabbed the whitespace token preceding a keyword and could yield
    ``"`"`` (a lone backtick, from the opening quote of `` `insert_time` ``) or
    ``"`insert_time`"`` (backtick-wrapped); this scans real column definitions
    instead and strips surrounding quotes/punctuation by construction.
    """
    time_cols = []
    seen = set()
    # A column definition: an optional backtick-quoted identifier then a type.
    col_pattern = re.compile(r'`?(\w+)`?\s+(\w+(?:\([^)]*\))?)')
    for m in col_pattern.finditer(schema):
        col, ctype = m.group(1), m.group(2)
        if col in seen:
            continue
        if col.lower() in _TIME_COL_NAMES or _TIME_TYPE_RE.match(ctype):
            seen.add(col)
            time_cols.append(col)
    return time_cols


def table_schema_query(tbl_name: str, connection: str = 'ck', force_refresh: bool = False) -> dict:
    cache_file = _get_cache_file_path('schema', connection)
    cache = _load_json_safe(cache_file)

    if not force_refresh and tbl_name in cache:
        entry = cache[tbl_name]
        if not _is_cache_expired(entry.get('cached_at', '')):
            return entry

    try:
        db = DBManager()
        client = getattr(db, connection)

        if hasattr(client, 'query_dataframe'):
            result = client.query_dataframe(f"SHOW CREATE TABLE {tbl_name}")
            schema = result.to_string() if not result.empty else ""
        else:
            result = client.execute(f"SHOW CREATE TABLE {tbl_name}")
            schema = str(result[0][0]) if result else ""

        schema = schema.strip().replace('statement\n', '').strip()
        if schema and schema[0].isdigit():
            idx = schema.find(' ')
            if idx > 0:
                schema = schema[idx:].strip()

        schema_info = {
            'table_name': tbl_name,
            'schema': schema,
            'parsed': _parse_create_table(schema),
            'cached_at': datetime.now().isoformat(),
            'time_columns': _extract_time_columns(schema),
        }

        cache[tbl_name] = schema_info
        _save_json_safe(cache_file, cache)
        return schema_info

    except Exception as e:
        if tbl_name in cache:
            return cache[tbl_name]
        return {'error': clean_db_error(str(e))}


# --- Table Info ---

def _resolve_connection_type(connection: str) -> str:
    """Resolve a connection name to its DBMS type ('clickhouse' | 'mysql').

    Replaces the brittle ``hasattr(client, 'query_dataframe')`` dispatch: BOTH
    Locked wrappers define ``query_dataframe`` (MySQL natively, ClickHouse via
    the adapter in connections/wrappers.py), so that attribute check *always*
    picked the MySQL branch — even for ClickHouse, which then fabricated a
    fake PRIMARY index from ClickHouse's stub ``SHOW INDEX``. This reads the
    section's declared ``type`` (with the section-name prefix fallback), the
    same source of truth ``find_table_query`` uses. Connection names use
    underscores; db.ini sections use dashes, so normalize the way
    DBManager.__getattr__ does.
    """
    cfg = DBManager.load_config(CONFIG_FILE)
    section = connection.replace('_', '-')
    return connection_type(cfg, section)


def _split_qualified_table(tbl_name: str) -> tuple:
    """Split a (possibly db-qualified) table name into (database, table).

    ``asia.elevenst_kr_seo_result`` -> ``('asia', 'elevenst_kr_seo_result')``.
    Backticks are stripped. Unqualified names return ``('', name)``.
    """
    tbl = tbl_name.strip().strip('`')
    if '.' in tbl:
        db_part, _, tbl_part = tbl.partition('.')
        return db_part.strip('`'), tbl_part.strip('`')
    return '', tbl


def _clickhouse_parts(client, tbl_name: str) -> list:
    """Active parts for a ClickHouse table, read from ``system.parts``.

    Returns up to 20 rows of ``{partition, name, rows, bytes_on_disk}`` — the
    real scan-cost signals (partition count, part count, row distribution) that
    ``SHOW PARTITIONS FROM <table>`` could never provide (it is invalid
    ClickHouse syntax: ``Code: 62. DB::Exception ... PARTITIONS``). Uses
    parameter binding for the identifiers so a mistyped/malicious name can never
    inject SQL, and falls back to ``currentDatabase()`` when the name is
    unqualified. Returns ``[]`` on error (never raises) so ``get_info`` still
    reports ``row_count``.
    """
    db_part, tbl_part = _split_qualified_table(tbl_name)
    if db_part:
        sql = (
            "SELECT partition, name, rows, bytes_on_disk "
            "FROM system.parts "
            "WHERE database = %(db)s AND table = %(tbl)s AND active = 1 "
            "ORDER BY partition, name "
            "LIMIT 20"
        )
        params = {'db': db_part, 'tbl': tbl_part}
    else:
        sql = (
            "SELECT partition, name, rows, bytes_on_disk "
            "FROM system.parts "
            "WHERE database = currentDatabase() AND table = %(tbl)s AND active = 1 "
            "ORDER BY partition, name "
            "LIMIT 20"
        )
        params = {'tbl': tbl_part}
    try:
        result = client.execute(sql, params, with_column_types=True)
    except Exception:
        return []
    if not result or not result[0]:
        return []
    data, columns = result
    col_names = [c[0] for c in columns]
    return [_sanitize_row(dict(zip(col_names, row))) for row in data]


def table_info_query(tbl_name: str, connection: str = 'ck', force_refresh: bool = False) -> dict:
    cache_file = _get_cache_file_path('info', connection)
    cache = _load_json_safe(cache_file)

    if not force_refresh and tbl_name in cache:
        entry = cache[tbl_name]
        if not _is_cache_expired(entry.get('cached_at', '')):
            return entry

    try:
        db = DBManager()
        client = getattr(db, connection)
        ctype = _resolve_connection_type(connection)
        info = {'table_name': tbl_name, 'cached_at': datetime.now().isoformat()}

        if ctype == 'mysql':
            # MySQL: SHOW INDEX returns real BTREE indexes with real cardinality.
            try:
                result = client.query_dataframe(f"SELECT COUNT(*) as cnt FROM {tbl_name}")
                info['row_count'] = int(result['cnt'].iloc[0]) if not result.empty else 0
            except Exception:
                info['row_count'] = None
            try:
                idx_result = client.query_dataframe(f"SHOW INDEX FROM {tbl_name}")
                if not idx_result.empty:
                    info['indexes'] = idx_result.to_dict('records')
            except Exception:
                info['indexes'] = []
        else:
            # ClickHouse has no MySQL-style PRIMARY index: PRIMARY KEY only sets
            # sparse-index granularity (no uniqueness, no lookup), and SHOW INDEX
            # returns a stub that fabricates a fake PRIMARY key with cardinality
            # 0 — which misleads agents into wrong join-cost estimates. So we do
            # NOT synthesize `indexes`; we report real scan-cost signals from
            # system.parts (partition / parts / rows per part) instead.
            try:
                result = client.execute(f"SELECT count() as cnt FROM {tbl_name}")
                info['row_count'] = result[0][0] if result else 0
            except Exception:
                info['row_count'] = None
            info['parts'] = _clickhouse_parts(client, tbl_name)

        cache[tbl_name] = info
        _save_json_safe(cache_file, cache)
        return info

    except Exception as e:
        if tbl_name in cache:
            return cache[tbl_name]
        return {'error': clean_db_error(str(e))}


# --- History ---

def _load_query_history(connection: str) -> dict:
    return _load_json_safe(_get_cache_file_path('query', connection))


def _save_query_history(connection: str, history: dict):
    if len(history) > MAX_HISTORY_ENTRIES:
        sorted_items = sorted(
            history.items(),
            key=lambda x: x[1].get('executed_at', ''),
            reverse=True
        )
        history = dict(sorted_items[:MAX_HISTORY_ENTRIES])
    _save_json_safe(_get_cache_file_path('query', connection), history)


def get_history_queries(connection: str = 'ck', tbl_name: Optional[str] = None) -> dict:
    history = _load_query_history(connection)
    if tbl_name:
        return {
            h: e for h, e in history.items()
            if e.get('table_name') == tbl_name
        }
    return history


# --- Database Discovery ---

def list_connections() -> str:
    """List available connection options from config. Returns JSON."""
    try:
        cfg = DBManager.load_config(CONFIG_FILE)
    except FileNotFoundError as e:
        return json.dumps({"connections": [], "count": 0, "error": clean_db_error(str(e))}, default=str)
    connections = []
    for section in cfg.sections():
        if section.lower().startswith('ssh:'):
            continue  # [ssh:*] blocks are bastion defs, not DB connections
        connections.append({
            'name': section,
            'type': connection_type(cfg, section),
        })
    return json.dumps({"connections": connections, "count": len(connections)}, default=str)


def check_connection_query(connection: str = 'ck') -> str:
    """Lightweight SELECT 1 round-trip to verify a connection is reachable.

    Returns JSON {connection, status:'ok', latency_ms} on success. Raises on
    failure so the caller (the MCP tool) can wrap it with an agent-actionable
    hint. Triggers the self-managed SSH tunnel for Auto-mode connections.
    """
    start = time.time()
    db = DBManager()
    client = getattr(db, connection)  # raises AttributeError if unknown; starts tunnel if Auto
    if hasattr(client, 'query_dataframe'):
        client.query_dataframe('SELECT 1')
    else:
        client.execute('SELECT 1')
    return json.dumps({
        "connection": connection,
        "status": "ok",
        "latency_ms": round((time.time() - start) * 1000, 1),
    })


def list_tables(connection: str = 'ck', database: str | None = None, like: str | None = None) -> str:
    """List tables in a database. Returns JSON with table names."""
    try:
        db = DBManager()
        client = getattr(db, connection)

        if hasattr(client, 'query_dataframe'):
            # MySQL
            sql = "SHOW TABLES"
            if database:
                sql = f"SHOW TABLES FROM `{database}`"
            if like:
                sql += f" LIKE '{like}'"
            result = client.query_dataframe(sql)
            if result.empty:
                return json.dumps({"tables": [], "count": 0})
            col = result.columns[0]
            tables = result[col].tolist()
        else:
            # ClickHouse
            if database:
                sql = f"SHOW TABLES FROM `{database}`"
            else:
                sql = "SHOW TABLES"
            if like:
                sql += f" LIKE '{like}'"
            result = client.execute(sql)
            tables = [row[0] for row in result] if result else []

        return json.dumps({"tables": tables, "count": len(tables)}, default=str)
    except Exception as e:
        return json.dumps({"error": clean_db_error(str(e))})


# --- Cross-Connection Table Locator ---

# Namespaces excluded from the global catalog scan (engine-internal, not user data).
_CK_SYSTEM_DBS = "('system','INFORMATION_SCHEMA','information_schema')"
_MYSQL_SYSTEM_SCHEMAS = "('information_schema','mysql','performance_schema','sys')"

# Minimum similarity to surface as a fuzzy (typo) match.
_FUZZY_MIN_SCORE = 0.4


def _sanitize_search(s: str) -> str:
    """Strip a search term to safe alphanumerics before embedding in catalog SQL."""
    return re.sub(r'[^\w]', '', s or '')


def _score_table_name(search: str, candidate: str) -> tuple:
    """Rank how close `candidate` is to `search`. Returns (score 0..1, match_type)."""
    s = search.lower()
    c = candidate.lower()
    if s == c:
        return 1.0, 'exact'
    if s in c:
        return 0.9, 'substring'
    if c in s:
        return 0.8, 'superstring'
    s_tokens = {t for t in re.split(r'[\W_]+', s) if t}
    c_tokens = {t for t in re.split(r'[\W_]+', c) if t}
    overlap = len(s_tokens & c_tokens)
    ratio = difflib.SequenceMatcher(None, s, c).ratio()
    if overlap:
        return min(0.89, 0.7 + 0.1 * overlap + 0.1 * ratio), 'token'
    return ratio, 'fuzzy'


def _catalog_query(ctype: str, search: str) -> str:
    """Fast-path SQL: server-side case-insensitive substring filter on table name."""
    if ctype == 'clickhouse':
        return (
            "SELECT database AS db_name, name AS tbl_name, engine "
            "FROM system.tables "
            f"WHERE positionCaseInsensitive(name, '{search}') > 0 "
            f"AND database NOT IN {_CK_SYSTEM_DBS}"
        )
    return (
        "SELECT table_schema AS db_name, table_name AS tbl_name, engine "
        "FROM information_schema.tables "
        f"WHERE LOWER(table_name) LIKE '%{search}%' "
        f"AND table_schema NOT IN {_MYSQL_SYSTEM_SCHEMAS}"
    )


def _full_catalog_query(ctype: str) -> str:
    """Unfiltered catalog for fuzzy fallback (typo recovery). System namespaces excluded."""
    if ctype == 'clickhouse':
        return (
            "SELECT database AS db_name, name AS tbl_name, engine "
            "FROM system.tables "
            f"WHERE database NOT IN {_CK_SYSTEM_DBS}"
        )
    return (
        "SELECT table_schema AS db_name, table_name AS tbl_name, engine "
        "FROM information_schema.tables "
        f"WHERE table_schema NOT IN {_MYSQL_SYSTEM_SCHEMAS}"
    )


def _exec_catalog(client, sql: str) -> list:
    """Run a catalog SELECT; return list of (database, table, engine) tuples."""
    if hasattr(client, 'query_dataframe'):
        df = client.query_dataframe(sql)
        if df is None or getattr(df, 'empty', True):
            return []
        out = []
        for row in df.itertuples(index=False, name=None):
            db_name = str(row[0]) if len(row) > 0 and row[0] is not None else ''
            tbl = str(row[1]) if len(row) > 1 and row[1] is not None else ''
            eng = str(row[2]) if len(row) > 2 and row[2] is not None else ''
            out.append((db_name, tbl, eng))
        return out
    result = client.execute(sql, with_column_types=True)
    if not result or not result[0]:
        return []
    data, _ = result
    return [
        (str(r[0]), str(r[1]), str(r[2]) if len(r) > 2 and r[2] is not None else '')
        for r in data
    ]


def _load_table_list_cache(connection: str) -> dict:
    return _load_json_safe(_get_cache_file_path('tables', connection))


def _save_table_list_cache(connection: str, tables: list):
    _save_json_safe(
        _get_cache_file_path('tables', connection),
        {'tables': tables, 'cached_at': datetime.now().isoformat()},
    )


def find_table_query(search: str, limit: int = 10) -> str:
    """Locate the table closest to `search` across ALL configured connections.

    Fans out over every connection, ranks candidates by name closeness
    (exact > substring > token > fuzzy), and returns each hit with its
    `connection` + `database` + `table` (+ `qualified_name`) so the caller can
    query the right place. Use this when unsure which connection holds a table,
    or when a query/definition lookup failed on a guessed connection.

    Fast path: server-side name filter (cheap). Fuzzy fallback: cached full
    table list scored client-side for typo recovery. Per-connection errors are
    swallowed and reported in `searched_connections` (one dead endpoint never
    blanks the result).
    """
    import configparser

    term = _sanitize_search(search)
    if not term:
        return json.dumps({"error": "search must contain alphanumerics", "matches": []})

    cfg = DBManager.load_config(CONFIG_FILE)

    matches: list = []
    searched: list = []

    for section in cfg.sections():
        if section.lower().startswith('ssh:'):
            continue  # [ssh:*] blocks are bastion defs, not DB connections
        try:
            ctype = connection_type(cfg, section)
        except Exception as e:
            searched.append({"name": section, "type": None,
                             "status": "error", "error": clean_db_error(str(e))})
            continue

        try:
            client = getattr(DBManager(), section)
        except Exception as e:
            searched.append({"name": section, "type": ctype,
                             "status": "error", "error": clean_db_error(str(e))})
            continue

        conn_err = None
        try:
            rows = _exec_catalog(client, _catalog_query(ctype, term))
        except Exception as e:
            rows = []
            conn_err = clean_db_error(str(e))

        # Fast path empty -> fuzzy fallback over cached full table list
        if not rows:
            cache = _load_table_list_cache(section)
            full = cache.get('tables') if (
                cache and not _is_cache_expired(cache.get('cached_at', ''))
            ) else None
            if full is None:
                try:
                    full = _exec_catalog(client, _full_catalog_query(ctype))
                    _save_table_list_cache(section, full)
                except Exception as e:
                    conn_err = conn_err or clean_db_error(str(e))
                    full = []
            rows = full

        scored = []
        for db_, tbl, eng in rows:
            score, mtype = _score_table_name(term, tbl)
            if score < _FUZZY_MIN_SCORE:
                continue
            scored.append({
                "connection": section,
                "database": db_,
                "table": tbl,
                "engine": eng,
                "score": round(score, 3),
                "match_type": mtype,
                "qualified_name": f"{db_}.{tbl}" if db_ else tbl,
            })
        matches.extend(scored)
        searched.append({
            "name": section, "type": ctype,
            "status": "error" if (conn_err and not scored) else "ok",
            "error": conn_err if not scored else None,
            "match_count": len(scored),
        })

    matches.sort(key=lambda m: m["score"], reverse=True)
    top = matches[: max(0, int(limit))]

    return json.dumps({
        "search": search,
        "normalized_search": term,
        "matches": top,
        "total_matches": len(matches),
        "searched_connections": searched,
    }, default=str)


# --- Explain ---

def explain_query(sql: str, connection: str = 'ck') -> str:
    """Run EXPLAIN on a query, return plan with cost estimates. Returns JSON."""
    if not _is_query_allowed(sql):
        raise AssertionError("Query must be a read-only operation.")

    explain_sql = f"EXPLAIN {sql}"

    try:
        db = DBManager()
        client = getattr(db, connection)

        if hasattr(client, 'query_dataframe'):
            result = client.query_dataframe(explain_sql)
            if result.empty:
                return json.dumps({"plan": [], "raw": ""})
            col = result.columns[0]
            plan_lines = result[col].tolist()
        else:
            result = client.execute(explain_sql)
            plan_lines = [row[0] for row in result] if result else []

        plan_text = '\n'.join(str(line) for line in plan_lines)

        cost_info = {}
        for line in plan_lines:
            line_str = str(line)
            for metric in ['rows', 'bytes', 'memory', 'cost']:
                m = re.search(rf'{metric}\s*[=:]\s*([\d.]+)', line_str, re.IGNORECASE)
                if m:
                    cost_info[metric] = cost_info.get(metric, 0) + float(m.group(1))

        return json.dumps({
            "plan": plan_text,
            "plan_lines": [str(l) for l in plan_lines],
            "cost_estimates": cost_info if cost_info else None,
        }, default=str)
    except Exception as e:
        return json.dumps({"error": clean_db_error(str(e))})


# --- Decimal Handling ---

def _sanitize_row(row_dict: dict) -> dict:
    """Convert Decimal values to float for clean JSON serialization."""
    from decimal import Decimal
    cleaned = {}
    for k, v in row_dict.items():
        if isinstance(v, Decimal):
            cleaned[k] = float(v)
        else:
            cleaned[k] = v
    return cleaned


# --- Query Execution ---

def execute_limit_query(sql: str, connection: str = 'ck') -> str:
    """Execute read-only query. Auto-adds LIMIT (default 10, max 2000). Returns JSON with metadata."""
    sql_trimmed = sql.strip().upper()

    if not _is_query_allowed(sql):
        raise AssertionError(
            f"Query must start with: SELECT, SHOW, DESCRIBE, DESC, EXPLAIN, "
            f"USE, WITH, or EXISTS. Got: {sql_trimmed[:50]}"
        )

    # LIMIT enforcement for SELECT and WITH (CTE) queries
    is_select_like = sql_trimmed.startswith('SELECT') or sql_trimmed.startswith('WITH')
    if is_select_like and 'LIMIT' not in sql_trimmed:
        sql = f"{sql.rstrip().rstrip(';')} LIMIT {DEFAULT_LIMIT}"
    elif is_select_like:
        match = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
        if match and int(match.group(1)) > MAX_LIMIT:
            sql = re.sub(r'LIMIT\s+\d+', f'LIMIT {MAX_LIMIT}', sql, flags=re.IGNORECASE)

    return execute_query(sql, connection)


def execute_query(sql: str, connection: str = 'ck') -> str:
    """Execute a read-only query and record it in history. Returns JSON."""
    if not _is_query_allowed(sql):
        raise AssertionError("Query must be a read-only operation.")

    start_time = time.time()
    query_hash = _generate_query_hash(sql)
    tbl_name = _get_table_name_from_sql(sql)

    try:
        db = DBManager()
        client = getattr(db, connection)
        result_json = _result_to_json(client, sql)
        query_time = time.time() - start_time

        # Inject query_time into result
        result_data = json.loads(result_json)
        result_data['query_time_ms'] = round(query_time * 1000, 1)

        # Save to history
        history = _load_query_history(connection)
        history[query_hash] = {
            'sql': _security.sanitize_text(sql),
            'query_time': query_time,
            'table_name': tbl_name,
            'executed_at': datetime.now().isoformat(),
        }
        _save_query_history(connection, history)

        return json.dumps(result_data, default=str)

    except Exception as e:
        return json.dumps({"error": clean_db_error(str(e))})
