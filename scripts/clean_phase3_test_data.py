"""清理 Phase 3 测试数据（含图谱）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.core.config import AppConfig
from app.core.database import init_db
from app.core.models import (ChatMessage, ChatSession, Event, Experience,
                             GraphEdge, GraphNode, MemoryShort, Notification,
                             Reminder, Task)

cfg = AppConfig.load()
engine, S = init_db(cfg.data_dir)
db = S()

# 测试会话
sess = db.query(ChatSession).filter(ChatSession.client_id == "graph-test").first()
if sess:
    db.query(ChatMessage).filter(ChatMessage.session_id == sess.id).delete()
    db.delete(sess)
# 测试事件/经验/任务（MindTrace 项目 synthetic）
db.query(Event).filter(Event.project == "MindTrace").delete(synchronize_session=False)
db.query(Task).filter(Task.project == "MindTrace").delete(synchronize_session=False)
db.query(Experience).filter(Experience.problem.like("%沙箱环境%")).delete(synchronize_session=False)
db.query(Notification).delete()
db.query(Reminder).delete()
db.query(MemoryShort).filter(MemoryShort.source.in_(["agent_chat", "terminal", "document"])).delete(synchronize_session=False)
# 图谱清空（重新构建）
db.query(GraphEdge).delete()
db.query(GraphNode).delete()
db.commit()
db.close()

with engine.begin() as conn:
    conn.execute(text("DELETE FROM vectors"))
    conn.execute(text("DELETE FROM consolidation_logs"))

print("cleaned（图谱已清空，将随真实使用自动重建）")
