"""清理 Phase 2 合规测试产生的数据。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.core.config import AppConfig
from app.core.database import init_db
from app.core.models import (Event, Experience, MemoryShort, Notification,
                             Reminder, Task)
from app.memory.vectors import VectorStore

cfg = AppConfig.load()
engine, S = init_db(cfg.data_dir)
db = S()

# 测试任务/经验/通知
db.query(Task).filter(Task.project.in_(["lifecycle-test", "life2", "recall-test", "demo4", "demo5", "demo6", "demo7"])).delete()
db.query(Experience).delete()
db.query(Notification).delete()
db.query(Reminder).delete()
# 测试事件（agent_chat 测试文本 + 热信号测试）
for kw in ("API 网关", "连接池", "方案A失败", "重试机制", "单元测试", "mock", "patch.object",
           "测试恢复", "情景摘要", "年度摘要", "截图", "端口被占用"):
    db.query(Event).filter(Event.activity.like(f"%{kw}%")).delete(synchronize_session=False)
# 保留采集产生的真实窗口记录（window 源），只清测试类
db.query(MemoryShort).filter(MemoryShort.source.in_(["agent_chat", "terminal", "document"])).delete(synchronize_session=False)
db.commit()
db.close()

# 清空测试向量（重建：保留手动记忆向量较麻烦，这里全清，用户记忆会随对话重新沉淀）
with engine.begin() as conn:
    conn.execute(text("DELETE FROM vectors"))
vs = VectorStore(engine)
print("cleaned, vectors:", vs.count())

# 删除测试截图
shots = Path(cfg.data_dir) / "screenshots"
if shots.exists():
    for f in shots.glob("*.png"):
        if "test" in f.name or "ai" in f.name:
            f.unlink(missing_ok=True)
print("screenshots cleaned")
