"""Shared config helpers for reading db.ini with cross-platform encoding support.

All modules that read db.ini should import resolve_env / read_config from here,
not duplicate them.
"""

import configparser
import os
import sys


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
