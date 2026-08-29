"""设置 API：监控目标（锁定单窗口/跟随前台/关闭）、采集授权开关、免打扰、一键清空、回收站。"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.models import Event, Experience, MemoryShort, Notification, Trash
from app.core.settings_store import get_bool, get_int, get_setting, set_setting

log = logging.getLogger("mindtrace.api.settings")
router = APIRouter(tags=["settings"])


# ---------- 监控目标 ----------

class MonitorConfig(BaseModel):
    mode: str = "follow"      # follow | locked | off
    pattern: str | None = None


@router.get("/api/monitor")
def get_monitor(request: Request):
    db = request.app.state.SessionLocal()
    try:
        mode = get_setting(db, "monitor_mode", None) or request.app.state.cfg.monitor_mode
        pattern = get_setting(db, "monitor_pattern", None) or request.app.state.cfg.locked_pattern
        paused = bool(getattr(request.app.state, "collector_paused", False))
        return {"mode": mode, "pattern": pattern or "", "paused": paused}
    finally:
        db.close()


@router.post("/api/monitor")
def set_monitor(req: MonitorConfig, request: Request):
    if req.mode not in ("follow", "locked", "off"):
        raise HTTPException(status_code=400, detail="非法模式")
    db = request.app.state.SessionLocal()
    try:
        set_setting(db, "monitor_mode", req.mode)
        set_setting(db, "monitor_pattern", req.pattern or "")
    finally:
        db.close()
    # 热更新采集器（无需重启）：关闭窗口采集时暂停窗口任务，开启时恢复
    monitor = getattr(request.app.state, "monitor", None)
    if monitor is not None:
        monitor.set_mode(req.mode, req.pattern or "")
    return {"ok": True, "mode": req.mode, "pattern": req.pattern or ""}


@router.get("/api/monitor/windows")
def list_windows(request: Request, limit: int = 60):
    """枚举当前打开的窗口（供"锁定单窗口"选择）。"""
    from app.collector.window import list_open_windows
    rows = list_open_windows(limit=limit)
    return {"windows": rows}


# ---------- 采集授权开关 ----------

class CollectorSettings(BaseModel):
    clipboard: bool | None = None
    browser_history: bool | None = None
    git: bool | None = None


@router.get("/api/collector/settings")
def get_collector_settings(request: Request):
    db = request.app.state.SessionLocal()
    try:
        return {
            "clipboard": get_bool(db, "collector_clipboard", request.app.state.cfg.clipboard_enabled),
            "browser_history": get_bool(db, "collector_browser", request.app.state.cfg.browser_history),
            "git": get_bool(db, "collector_git", request.app.state.cfg.git_enabled),
        }
    finally:
        db.close()


@router.post("/api/collector/settings")
def set_collector_settings(req: CollectorSettings, request: Request):
    db = request.app.state.SessionLocal()
    try:
        if req.clipboard is not None:
            set_setting(db, "collector_clipboard", "1" if req.clipboard else "0")
        if req.browser_history is not None:
            set_setting(db, "collector_browser", "1" if req.browser_history else "0")
        if req.git is not None:
            set_setting(db, "collector_git", "1" if req.git else "0")
    finally:
        db.close()
    monitor = getattr(request.app.state, "monitor", None)
    if monitor is not None:
        if req.clipboard is not None:
            monitor.live_clipboard = req.clipboard
        if req.browser_history is not None:
            monitor.live_browser = req.browser_history
        if req.git is not None:
            monitor.live_git = req.git
    return {"ok": True}


# ---------- 免打扰时段 ----------

class QuietHours(BaseModel):
    start: str = "23:00"
    end: str = "07:00"


@router.get("/api/quiet-hours")
def get_quiet_hours(request: Request):
    db = request.app.state.SessionLocal()
    try:
        return {
            "start": get_setting(db, "quiet_start", "23:00"),
            "end": get_setting(db, "quiet_end", "07:00"),
        }
    finally:
        db.close()


@router.post("/api/quiet-hours")
def set_quiet_hours(req: QuietHours, request: Request):
    db = request.app.state.SessionLocal()
    try:
        set_setting(db, "quiet_start", req.start)
        set_setting(db, "quiet_end", req.end)
    finally:
        db.close()
    notify = getattr(request.app.state, "notify", None)
    if notify is not None:
        notify.quiet_start = req.start
        notify.quiet_end = req.end
    return {"ok": True}


# ---------- 一键清空记忆 ----------

@router.post("/api/settings/clear-memory")
def clear_memory(request: Request):
    db = request.app.state.SessionLocal()
    try:
        db.query(Event).delete()
        db.query(Experience).delete()
        db.query(MemoryShort).delete()
        db.query(Trash).delete()
        db.commit()
    finally:
        db.close()
    vectors = getattr(request.app.state, "vectors", None)
    if vectors is not None:
        try:
            from sqlalchemy import text
            with vectors.engine.begin() as conn:
                conn.execute(text("DELETE FROM vectors"))
        except Exception as exc:  # noqa: BLE001
            log.warning("清空向量失败: %s", exc)
    return {"ok": True}


# ---------- 回收站 ----------

@router.get("/api/trash")
def list_trash(request: Request, limit: int = 100):
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(Trash).order_by(Trash.id.desc()).limit(limit).all()
        return {
            "trash": [
                {
                    "id": t.id,
                    "target_type": t.target_type,
                    "target_id": t.target_id,
                    "payload": t.payload,
                    "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                }
                for t in rows
            ]
        }
    finally:
        db.close()


@router.post("/api/trash/{trash_id}/restore")
def restore_trash(trash_id: int, request: Request):
    """从回收站恢复（降级不删除，可逆）。"""
    db = request.app.state.SessionLocal()
    try:
        t = db.get(Trash, trash_id)
        if t is None:
            raise HTTPException(status_code=404, detail="回收站记录不存在")
        payload = t.payload or {}
        model = payload.get("model")
        text = payload.get("text") or ""
        importance = payload.get("importance", 0.5)
        time_str = payload.get("time")
        try:
            ts = datetime.fromisoformat(time_str) if time_str else datetime.now()
        except Exception:
            ts = datetime.now()

        restored = None
        if model == "events":
            restored = Event(time=ts, app="restored", activity=text, intent=text,
                             importance=importance, source="restored")
        elif model == "experiences":
            restored = Experience(problem=text, importance=importance, status="pending")
        elif model == "memories_short":
            restored = MemoryShort(time=ts, source="restored", text=text, importance=importance)
        if restored is None:
            raise HTTPException(status_code=400, detail="该类型暂不支持恢复")
        db.add(restored)
        db.flush()
        db.delete(t)
        db.commit()
        restored_id = restored.id
    finally:
        db.close()

    # 恢复向量
    memory = getattr(request.app.state, "memory", None)
    vectors = getattr(request.app.state, "vectors", None)
    if memory is not None and vectors is not None:
        if model in ("events", "experiences"):
            vec = memory.embed_text(text)
            if vec is not None:
                vectors.add(model.rstrip("s"), restored_id, vec)
    return {"ok": True, "restored_id": restored_id, "type": model}


# ---------- 通知动作（保留/仅记录/丢弃 + 联想反馈） ----------

class ExperienceAction(BaseModel):
    action: str  # confirm（保留）/ keep（仅记录）/ discard（丢弃）


@router.post("/api/notifications/{notif_id}/experience-action")
def experience_action(notif_id: int, req: ExperienceAction, request: Request):
    """攻克/经验候选通知的三分动作 → 更新关联经验状态。"""
    db = request.app.state.SessionLocal()
    try:
        n = db.get(Notification, notif_id)
        if n is None:
            raise HTTPException(status_code=404, detail="通知不存在")
        action = (n.action or {})
        exp = None
        if action.get("type") == "experience":
            task_id = action.get("task_id")
            if task_id:
                exp = db.query(Experience).filter(Experience.source_task_id == task_id).first()
        elif action.get("type") == "candidate":
            problem = action.get("problem")
            solution = action.get("solution")
            if req.action == "confirm" and problem:
                exp = Experience(problem=problem, final_solution=solution, importance=0.6,
                                 confirmed=True, status="confirmed")
                db.add(exp)
        if exp is not None and req.action in ("confirm", "keep", "discard"):
            if req.action == "confirm":
                exp.status, exp.confirmed = "confirmed", True
            elif req.action == "keep":
                exp.status, exp.confirmed = "draft", False   # 仅记录在案
            elif req.action == "discard":
                exp.status, exp.confirmed = "discarded", False
        n.status = "done"
        db.commit()
        return {"ok": True}
    finally:
        db.close()


class RecallFeedback(BaseModel):
    useful: bool


@router.post("/api/notifications/{notif_id}/feedback")
def recall_feedback(notif_id: int, req: RecallFeedback, request: Request):
    """联想提醒反馈回流：有用 → 经验引用 +1；无关 → 记录负样本。"""
    db = request.app.state.SessionLocal()
    try:
        n = db.get(Notification, notif_id)
        if n is None:
            raise HTTPException(status_code=404, detail="通知不存在")
        if n.kind == "recall_reminder":
            from app.core.models import Experience, UserSignal
            if req.useful:
                # 提升被联想命中的经验引用数（w6）
                exp = db.query(Experience).order_by(Experience.importance.desc()).first()
                if exp is None:
                    exp = db.query(Experience).order_by(Experience.id.desc()).first()
                if exp is not None:
                    exp.ref_count = (exp.ref_count or 0) + 1
            db.add(UserSignal(kind="recall_feedback", target_type="notification", target_id=notif_id,
                              value=1.0 if req.useful else 0.0,
                              note="useful" if req.useful else "useless"))
        n.status = "done"
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ---------- 实时联想常驻列表 ----------

@router.get("/api/recall/recent")
def recent_recalls(request: Request, limit: int = 5):
    db = request.app.state.SessionLocal()
    try:
        from app.core.models import Notification
        rows = (
            db.query(Notification)
            .filter(Notification.kind == "recall_reminder")
            .order_by(Notification.id.desc())
            .limit(limit)
            .all()
        )
        return {
            "recalls": [
                {"id": n.id, "title": n.title, "body": n.body, "status": n.status,
                 "created_at": n.created_at.isoformat() if n.created_at else None}
                for n in rows
            ]
        }
    finally:
        db.close()


# ---------- 重要性权重（w1-w6，UI 可见可改） ----------

class WeightsRequest(BaseModel):
    weights: dict


@router.get("/api/scoring/weights")
def get_scoring_weights(request: Request):
    from app.memory.scoring import get_weights
    db = request.app.state.SessionLocal()
    try:
        return {"weights": get_weights(db)}
    finally:
        db.close()


@router.post("/api/scoring/weights")
def set_scoring_weights(req: WeightsRequest, request: Request):
    from app.memory.scoring import save_weights
    db = request.app.state.SessionLocal()
    try:
        save_weights(db, req.weights)
        return {"ok": True}
    finally:
        db.close()
