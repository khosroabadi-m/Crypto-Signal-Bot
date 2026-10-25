# state.py — وضعیت کوچک اجرا (آخرین گزارش شبانه و ...) روی state.json

import json
import os

from . import settings


def _path():
    return settings.STATE_JSON


def read() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write(d: dict):
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    cur = read()
    cur.update(d)
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)


def get(key, default=None):
    return read().get(key, default)


def set_key(key, value):
    write({key: value})
