"""初始化数据库（建表）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import AppConfig
from app.core.database import init_db


def main() -> None:
    cfg = AppConfig.load()
    engine, _ = init_db(cfg.data_dir)
    print(f"[ok] 数据库已就绪：{cfg.data_dir / 'mindtrace.db'}")


if __name__ == "__main__":
    main()
