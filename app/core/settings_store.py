"""键值设置存取（Setting 表）。"""
from __future__ import annotations

from typing import Any

from app.core.models import Setting


def get_setting(db, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    return row.value if row is not None else default


def set_setting(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)
    db.commit()


def get_int(db, key: str, default: int = 0) -> int:
    v = get_setting(db, key)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def get_bool(db, key: str, default: bool = False) -> bool:
    v = get_setting(db, key)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")
