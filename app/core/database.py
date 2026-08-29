"""SQLAlchemy 数据库：engine / session / 建表 / 轻量迁移。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from .models import Base


def _migrate(engine) -> None:
    """轻量迁移：为已有表补充缺失列（Phase 0/1 阶段够用）。"""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    for table, col in (("chat_sessions", "updated_at"), ("tasks", "updated_at")):
        if table in tables:
            cols = {c["name"] for c in insp.get_columns(table)}
            if col not in cols:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} DATETIME"))


def init_db(data_dir: Path):
    """初始化数据目录与 SQLite 数据库，返回 (engine, SessionLocal)。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "mindtrace.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.begin() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))  # 读写并发友好
    Base.metadata.create_all(engine)
    _migrate(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, SessionLocal
