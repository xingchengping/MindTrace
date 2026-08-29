"""联想生命周期测试：反馈回流 + 常驻列表。"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import AppConfig
from app.core.database import init_db
from app.core.models import Experience, Notification, Task
from app.desktop.notify import NotifyService
from app.llm.engine import LlamaServer
from app.memory.service import MemoryService
from app.memory.vectors import VectorStore
from app.tasks.engine import TaskEngine

BASE = "http://127.0.0.1:4000"


def req(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main():
    cfg = AppConfig.load()
    engine, S = init_db(cfg.data_dir)
    llm = LlamaServer(server_path="x", model_path=None, port=cfg.server_port, n_ctx=128)
    llm.attach_external(wait_seconds=5)
    emb = LlamaServer(server_path="x", model_path=None, port=cfg.emb_server_port, mode="embedding", n_ctx=128)
    emb.attach_external(wait_seconds=5)
    vectors = VectorStore(engine)
    memory = MemoryService(emb, vectors, S, top_k=5, min_score=0.35)
    notify = NotifyService(S, toasts_enabled=False)
    te = TaskEngine(cfg, S, memory, llm, notify)
    te._recall_minutes = 15
    te._recall_threshold = 0.75

    exp = memory.add_experience(problem="数据库连接池耗尽导致服务不可用", final_solution="连接池预热+重试", confirmed=True)
    db = S()
    task = Task(task_name="连接池问题", project="life2", stage="blocked", status="active",
                current_problem="数据库连接池耗尽导致服务不可用",
                context={"blocked_since": (datetime.now() - timedelta(minutes=20)).isoformat()})
    db.add(task)
    db.commit()
    db.refresh(task)
    db.close()
    te._maybe_recall_reminder(S(), task)

    db = S()
    n = db.query(Notification).filter(Notification.kind == "recall_reminder").first()
    exp0 = db.query(Experience).filter(Experience.id == exp.id).first()
    print("1. recall notif:", bool(n), "| exp ref_count before:", exp0.ref_count)
    nid = n.id
    db.close()

    req("POST", f"/api/notifications/{nid}/feedback", {"useful": True})
    db = S()
    exp1 = db.query(Experience).filter(Experience.id == exp.id).first()
    print("2. exp ref_count after useful:", exp1.ref_count)
    db.close()

    data = req("GET", "/api/recall/recent")
    recalls = data.get("recalls", [])
    print("3. recall list:", len(recalls), "| first status:", recalls[0]["status"] if recalls else None)


if __name__ == "__main__":
    main()
