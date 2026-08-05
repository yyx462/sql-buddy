"""Shared config helpers for reading db.ini with cross-platform encoding support.

All modules that touch db.ini resolve its location and parse it through the
helpers here, never duplicating the path arithmetic.

Config-location resolution
(see docs/adr/0002-config-discovery-and-invocation-mode-on-publish.md):
  config_path()        - first existing candidate, for READ
  config_write_path()  - the detected home, for WRITE (the init wizard)
  precedence: $SQL_BUDDY_CONFIG > platformdirs user dir > checkout-relative
"""

import configparser
import os
import sys

APP_NAME = "sql-buddy"
_CONFIG_FILENAME = "db.ini"
_ENV_OVERRIDE = "SQL_BUDDY_CONFIG"

# config_util.py ships inside the `connections` package, so the checkout-relative
# config lives at <this dir>/config/db.ini.
_HERE = os.path.dirname(os.path.abspath(__file__))
_CHECKOUT_CONFIG_PATH = os.path.join(_HERE, "config", _CONFIG_FILENAME)
# A checkout is signalled by a `.git` entry at the repo root (a directory for a
# plain clone, a file for a linked worktree), or by a writable config dir
# holding the example. Wheels/sdists include neither, so installed copies never
# match here and correctly fall through to the per-user config dir.
_REPO_ROOT = os.path.dirname(_HERE)


def _env_config_path():
    """$SQL_BUDDY_CONFIG may name the file directly or a directory holding it."""
    raw = os.environ.get(_ENV_OVERRIDE)
    if not raw:
        return None
    p = os.path.expanduser(raw)
    if os.path.isdir(p):
        return os.path.join(p, _CONFIG_FILENAME)
    return p


def _user_config_path():
    """Per-user config dir, or None if platformdirs isn't importable.

    Returning None lets config_path()/config_write_path() degrade to the
    checkout-relative path when the dependency is somehow absent, so a
    platformdirs install failure can never break an existing checkout user.
    """
    try:
        import platformdirs
    except ImportError:
        return None
    return os.path.join(platformdirs.user_config_dir(APP_NAME), _CONFIG_FILENAME)


def is_checkout() -> bool:
    """Whether sql-buddy is running from a source checkout vs an install.

    Public so the CLI's invocation-mode detection can reuse the same signal
    (see cli._mcp_entry). See _is_checkout docstring location for the rationale.
    """
    if os.path.exists(os.path.join(_REPO_ROOT, ".git")):
        return True
    cfg_dir = os.path.join(_HERE, "config")
    example = os.path.join(cfg_dir, "db.ini.example")
    return os.path.isfile(example) and os.access(cfg_dir, os.W_OK)


def config_write_path() -> str:
    """Where the init wizard should write db.ini.

    $SQL_BUDDY_CONFIG wins; otherwise a checkout writes checkout-relative (dev
    back-compat) and an install writes to the per-user config dir.
    """
    env = _env_config_path()
    if env is not None:
        return env
    if is_checkout():
        return _CHECKOUT_CONFIG_PATH
    return _user_config_path() or _CHECKOUT_CONFIG_PATH


def config_path() -> str:
    """Where to READ db.ini from.

    $SQL_BUDDY_CONFIG is strict (an explicit override). Otherwise the first
    existing candidate among the per-user dir and the checkout-relative path. If
    none exist yet, returns config_write_path() so callers can report the place
    the user should create.
    """
    env = _env_config_path()
    if env is not None:
        return env
    for candidate in (_user_config_path(), _CHECKOUT_CONFIG_PATH):
        if candidate is not None and os.path.isfile(candidate):
            return candidate
    return config_write_path()


# Historical env override kept for back-compat: db_query used it as the root for
# ALL its caches (query_history + db_info_cache + db_schema_cache + table_list).
_CACHE_ENV_OVERRIDE = "DB_QUERY_HISTORY_DIR"
_CACHE_DIRNAME = "database"


def _user_cache_root():
    """Per-user cache dir, or None if platformdirs isn't importable."""
    try:
        import platformdirs
    except ImportError:
        return None
    return platformdirs.user_cache_dir(APP_NAME)


def cache_dir_path() -> str:
    """Where sql-buddy writes runtime caches (query history, schema/info caches).

    $DB_QUERY_HISTORY_DIR (historical override) > per-user cache dir (when not a
    checkout) > the checkout-relative `database/` dir. Installed copies must
    never write into the read-only package cache, so a non-checkout resolves to
    the per-user cache dir; if platformdirs is somehow absent it falls back to
    the checkout-relative path so a missing dep never crashes the server.
    """
    env = os.environ.get(_CACHE_ENV_OVERRIDE)
    if env:
        return os.path.expanduser(env)
    if is_checkout():
        return os.path.join(_REPO_ROOT, _CACHE_DIRNAME)
    return _user_cache_root() or os.path.join(_REPO_ROOT, _CACHE_DIRNAME)


def resolve_env(value: str) -> str:
    if isinstance(value, str) and value.startswith('$env:'):
        var_name = value[5:]
        resolved = os.environ.get(var_name)
        if resolved is None:
            raise EnvironmentError(
                f'[配置缺失] $env:{var_name} 未在环境中设置。请确认 .env 文件存在（参考 .env.example）或手动设置该环境变量。'
            )
        return resolved
    return value


def read_config(config_file: str) -> configparser.RawConfigParser:
    cfg = configparser.RawConfigParser()
    with open(config_file, "rb") as f:
        raw = f.read()
    encodings = ["utf-8-sig", "utf-8", "gbk"]
    if sys.platform == "win32":
        encodings.append("mbcs")
    for enc in encodings:
        try:
            cfg.read_string(raw.decode(enc))
            return cfg
        except UnicodeDecodeError:
            continue
    cfg.read_string(raw.decode("utf-8", errors="replace"))
    return cfg
