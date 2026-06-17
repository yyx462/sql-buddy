"""SQL Buddy - Database query logic for the MCP server."""

import hashlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_conn_locked import LockedDBManager as DBManager, connection_type

# Cache directories
ROOT_DIR = os.environ.get(
    'DB_QUERY_HISTORY_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database')
)
QUERY_HISTORY_DIR = os.path.join(ROOT_DIR, 'query_history')
DB_INFO_CACHE_DIR = os.path.join(ROOT_DIR, 'db_info_cache')
DB_SCHEMA_CACHE_DIR = os.path.join(ROOT_DIR, 'db_schema_cache')

CACHE_EXPIRE_DAYS = 3
MAX_HISTORY_ENTRIES = 1000
SLOW_QUERY_THRESHOLD_SEC = 30
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'db_conn_locked', 'config', 'db.ini')


def init_dirs():
    for d in [QUERY_HISTORY_DIR, DB_INFO_CACHE_DIR, DB_SCHEMA_CACHE_DIR]:
        os.makedirs(d, exist_ok=True)


def _get_cache_file_path(cache_type: str, connection: str) -> str:
    if cache_type == 'query':
        return os.path.join(QUERY_HISTORY_DIR, f'{connection}_query_history.json')
    elif cache_type == 'info':
        return os.path.join(DB_INFO_CACHE_DIR, f'{connection}_table_info.json')
    elif cache_type == 'schema':
        return os.path.join(DB_SCHEMA_CACHE_DIR, f'{connection}_schema.json')
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


# --- Union-Find for optimization tracking ---

class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
            return x
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: str, y: str):
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self.parent[ry] = rx

    def is_same_group(self, x: str, y: str) -> bool:
        return self.find(x) == self.find(y)


_uf = UnionFind()


# --- Schema ---

def _parse_create_table(schema: str) -> dict:
    schema_clean = schema.replace('\\n', '\n')
    result = {
        'table_name': '', 'engine': '', 'partition': '',
        'order_by': '', 'columns': [], 'projections': []
    }

    m = re.search(r'CREATE\s+TABLE\s+([\w.`]+)', schema_clean, re.IGNORECASE)
    if m:
        result['table_name'] = m.group(1).strip('`')

    m = re.search(r'ENGINE\s*=\s*(\w+)', schema_clean, re.IGNORECASE)
    if m:
        result['engine'] = m.group(1)

    m = re.search(
        r'PARTITION\s+BY\s+([^\n]+?)(?=\n\s*(?:ORDER|PRIMARY|KEY|ENGINE|SETTINGS)|\n\))',
        schema_clean, re.IGNORECASE
    )
    if m:
        result['partition'] = m.group(1).strip()

    m = re.search(
        r'ORDER\s+BY\s+([^\n]+?)(?=\n\s*(?:PRIMARY|KEY|SETTINGS)|\n\))',
        schema_clean, re.IGNORECASE
    )
    if m:
        result['order_by'] = m.group(1).strip()

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


def _extract_time_columns(schema: str) -> list:
    time_cols = []
    schema_lower = schema.lower()
    for kw in ['create_time', 'insert_time', 'update_time', 'date', 'datetime', 'timestamp']:
        if kw in schema_lower:
            idx = schema_lower.find(kw)
            segment = schema[max(0, idx - 30):idx]
            parts = segment.replace(',', ' ').split()
            if parts:
                col = parts[-1].strip()
                if col and col not in time_cols:
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
        return {'error': str(e)}


# --- Table Info ---

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
        info = {'table_name': tbl_name, 'cached_at': datetime.now().isoformat()}

        if hasattr(client, 'query_dataframe'):
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
            try:
                result = client.execute(f"SELECT count() as cnt FROM {tbl_name}")
                info['row_count'] = result[0][0] if result else 0
            except Exception:
                info['row_count'] = None
            try:
                part_result = client.execute(f"SHOW PARTITIONS FROM {tbl_name}")
                info['partitions'] = str(part_result[:5])
            except Exception:
                info['partitions'] = None

        cache[tbl_name] = info
        _save_json_safe(cache_file, cache)
        return info

    except Exception as e:
        if tbl_name in cache:
            return cache[tbl_name]
        return {'error': str(e)}


# --- Query Optimization ---

def query_optimize(tbl_name: str, query: str, connection: str = 'ck') -> str:
    schema_info = table_schema_query(tbl_name, connection)
    optimized = query

    time_cols = schema_info.get('time_columns', [])
    if time_cols and 'WHERE' not in query.upper():
        optimized = f"-- Suggestion: Add time filter on {time_cols[0]}\n{query}"

    if 'ORDER BY' in query.upper() and 'LIMIT' not in query.upper():
        optimized = f"{query.rstrip().rstrip(';')} LIMIT 1000"

    return optimized


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
    import configparser
    cfg = configparser.RawConfigParser()
    cfg.read(CONFIG_FILE)
    connections = []
    for section in cfg.sections():
        connections.append({
            'name': section,
            'type': connection_type(cfg, section),
        })
    return json.dumps({"connections": connections, "count": len(connections)}, default=str)


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
        return json.dumps({"error": str(e)})


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
        return json.dumps({"error": str(e)})


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
    """Execute read-only query. Auto-adds LIMIT (default 10, max 100). Returns JSON with metadata."""
    sql_trimmed = sql.strip().upper()

    if not _is_query_allowed(sql):
        raise AssertionError(
            f"Query must start with: SELECT, SHOW, DESCRIBE, DESC, EXPLAIN, "
            f"USE, WITH, or EXISTS. Got: {sql_trimmed[:50]}"
        )

    # LIMIT enforcement for SELECT and WITH (CTE) queries
    is_select_like = sql_trimmed.startswith('SELECT') or sql_trimmed.startswith('WITH')
    if is_select_like and 'LIMIT' not in sql_trimmed:
        sql = f"{sql.rstrip().rstrip(';')} LIMIT 10"
    elif is_select_like:
        match = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
        if match and int(match.group(1)) > 100:
            sql = re.sub(r'LIMIT\s+\d+', 'LIMIT 100', sql, flags=re.IGNORECASE)

    return execute_query(sql, connection)


def execute_query(sql: str, connection: str = 'ck') -> str:
    """Execute query with history + optimization tracking. Returns JSON."""
    if not _is_query_allowed(sql):
        raise AssertionError("Query must be a read-only operation.")

    start_time = time.time()
    query_hash = _generate_query_hash(sql)
    tbl_name = _get_table_name_from_sql(sql)

    # Check for optimized version
    history = _load_query_history(connection)
    if query_hash in history:
        entry = history[query_hash]
        better_idx = entry.get('better_sql_index')
        if better_idx and better_idx in history:
            if not _uf.is_same_group(query_hash, better_idx):
                optimized_sql = history[better_idx].get('sql', sql)
                if optimized_sql != sql:
                    sql = optimized_sql

    try:
        db = DBManager()
        client = getattr(db, connection)
        result_json = _result_to_json(client, sql)
        query_time = time.time() - start_time

        # Inject query_time into result
        result_data = json.loads(result_json)
        result_data['query_time_ms'] = round(query_time * 1000, 1)
        result_json = json.dumps(result_data, default=str)

        # Save to history
        history[query_hash] = {
            'sql': sql,
            'query_time': query_time,
            'table_name': tbl_name,
            'executed_at': datetime.now().isoformat(),
            'better_sql_index': history.get(query_hash, {}).get('better_sql_index'),
        }

        # Slow query optimization
        if query_time > SLOW_QUERY_THRESHOLD_SEC and tbl_name:
            optimized_sql = query_optimize(tbl_name, sql, connection)
            if optimized_sql != sql:
                new_hash = _generate_query_hash(optimized_sql)
                history[new_hash] = {
                    'sql': optimized_sql,
                    'query_time': None,
                    'table_name': tbl_name,
                    'executed_at': datetime.now().isoformat(),
                    'better_sql_index': None,
                    'optimized_from': query_hash,
                }
                _uf.union(query_hash, new_hash)
                history[query_hash]['better_sql_index'] = new_hash

                # Add optimization hint to result
                result_data = json.loads(result_json)
                result_data['optimization_hint'] = (
                    f"Query took {query_time:.1f}s. Consider: {optimized_sql[:200]}"
                )
                result_json = json.dumps(result_data, default=str)

        _save_query_history(connection, history)
        return result_json

    except Exception as e:
        return json.dumps({"error": str(e)})
