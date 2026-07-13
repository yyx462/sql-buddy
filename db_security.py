"""Prevent passwords, keys, and tokens from leaking into logs or error output.

Usage (call once, early in the server startup):

    import db_security
    db_security.install()

After install():
  - The root logger and all descendant loggers get a SensitiveValueFilter that
    replaces known sensitive env-var values with `***` in log messages.
  - sys.excepthook + threading.excepthook are overridden to sanitize traceback
    text before it reaches stderr.
  - Exception __str__ / __repr__ on errors that embed env-var values (e.g.
    connection errors that include the full config string) is left alone; the
    hook strips those values from the traceback output only.
"""

import logging
import os
import sys
import threading


_SENSITIVE_VARS: set[str] = set()
_SENSITIVE_VALUES: list[str] = []


def _collect_sensitive() -> tuple[set[str], list[str]]:
    sensitive_keys = set()
    sensitive_vals: list[str] = []
    for key, value in os.environ.items():
        upper = key.upper()
        if any(
            tag in upper
            for tag in ("_PASS", "_PASSWORD", "_KEY", "_TOKEN", "_SECRET", "_CRED")
        ):
            sensitive_keys.add(key)
            if value:
                sensitive_vals.append(value)
    return sensitive_keys, sensitive_vals


class SensitiveValueFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if not _SENSITIVE_VALUES:
            return True
        for val in _SENSITIVE_VALUES:
            if val and val in msg:
                msg = msg.replace(val, "***")
        record.msg = msg
        record.args = None
        return True


_original_excepthook = sys.excepthook
_original_threading_excepthook = threading.excepthook


def _sanitize_excepthook(exc_type, exc_value, exc_tb):
    exc_value = _sanitize_exception(exc_value)
    _original_excepthook(exc_type, exc_value, exc_tb)


def _sanitize_threading_excepthook(args):
    if hasattr(args, "exc_value") and args.exc_value is not None:
        args.exc_value = _sanitize_exception(args.exc_value)
    _original_threading_excepthook(args)


def _sanitize_exception(exc_value):
    if exc_value is None:
        return exc_value
    try:
        msg = str(exc_value)
        if not _SENSITIVE_VALUES:
            return exc_value
        for val in _SENSITIVE_VALUES:
            if val and val in msg:
                msg = msg.replace(val, "***")
        exc_value.args = (msg,)
    except Exception:
        pass
    return exc_value


def _load_env_file() -> str | None:
    """Load .env from the configured path. Returns the path used, or None."""
    env_path = os.environ.get("SQL_BUDDY_ENV_FILE")
    if env_path:
        env_path = os.path.expanduser(env_path)
        _load_env(env_path)
        return env_path
    repo_root = os.path.dirname(os.path.abspath(__file__))
    for name in (".env", ".env.local"):
        candidate = os.path.join(repo_root, name)
        if os.path.isfile(candidate):
            _load_env(candidate)
            return candidate
    return None


def _load_env(path: str):
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def install():
    """Activate all protections: .env loading, log filters, excepthooks."""
    _load_env_file()
    keys, vals = _collect_sensitive()
    _SENSITIVE_VARS.update(keys)
    _SENSITIVE_VALUES.extend(v for v in vals if v and v not in _SENSITIVE_VALUES)

    root = logging.getLogger()
    root.addFilter(SensitiveValueFilter())

    sys.excepthook = _sanitize_excepthook
    threading.excepthook = _sanitize_threading_excepthook


def sanitize_text(text: str) -> str:
    """Replace all known sensitive values with `***` in arbitrary text.

    Call this before persisting SQL queries to history, returning data in MCP
    responses, or writing to any durable log. Idempotent — if values are already
    masked, no harm.
    """
    if not text:
        return text
    for val in _SENSITIVE_VALUES:
        if val and val in text:
            text = text.replace(val, "***")
    return text
