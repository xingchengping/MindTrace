"""清理测试数据（测试专用）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.core.config import AppConfig
from app.core.database import init_db
from app.core.models import Event, Experience, Notification, Reminder, Task
from app.memory.vectors import VectorStore

cfg = AppConfig.load()
engine, S = init_db(cfg.data_dir)
db = S()
db.query(Task).filter(Task.project == "recall-test").delete()
db.query(Experience).delete()
db.query(Notification).delete()
db.query(Reminder).delete()
db.query(Event).filter(Event.activity.like("测试恢复%")).delete()
db.commit()
db.close()

vs = VectorStore(engine)
with engine.begin() as conn:
    for etype in ("event", "experience"):
        conn.execute(text(f"DELETE FROM vectors WHERE entity_type='{etype}'"))
print("cleaned, vectors:", vs.count())
