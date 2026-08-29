"""记忆相关 API：事件时间线 / 经验库 / 任务面板 / 反馈。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.models import ChatMessage, Event, Experience, Task, UserSignal

router = APIRouter(tags=["memory"])


# ---------- 事件（记忆时间线） ----------

@router.get("/api/events")
def list_events(request: Request, limit: int = 200, project: str | None = None, source: str | None = None):
    db = request.app.state.SessionLocal()
    try:
        q = db.query(Event)
        if project:
            q = q.filter(Event.project == project)
        if source:
            q = q.filter(Event.source == source)
        rows = q.order_by(Event.time.desc()).limit(min(limit, 1000)).all()
        return {
            "events": [
                {
                    "id": e.id,
                    "time": e.time.isoformat() if e.time else None,
                    "app": e.app,
                    "activity": e.activity,
                    "intent": e.intent,
                    "project": e.project,
                    "importance": e.importance,
                    "source": e.source,
                }
                for e in rows
            ]
        }
    finally:
        db.close()


# ---------- 经验库 ----------

@router.get("/api/experiences")
def list_experiences(request: Request):
    db = request.app.state.SessionLocal()
    try:
        rows = db.query(Experience).order_by(Experience.importance.desc(), Experience.id.desc()).all()
        return {
            "experiences": [
                {
                    "id": e.id,
                    "problem": e.problem,
                    "background": e.background,
                    "attempts": e.attempts,
                    "failed_solutions": e.failed_solutions,
                    "final_solution": e.final_solution,
                    "scenarios": e.scenarios,
                    "advice": e.advice,
                    "tags": e.tags,
                    "importance": e.importance,
                    "confirmed": e.confirmed,
                    "status": e.status,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "ref_count": e.ref_count,
                }
                for e in rows
            ]
        }
    finally:
        db.close()


class ExperienceUpdate(BaseModel):
    status: str  # confirmed / discarded / draft
    importance: float | None = None


@router.patch("/api/experiences/{exp_id}")
def update_experience(exp_id: int, req: ExperienceUpdate, request: Request):
    db = request.app.state.SessionLocal()
    try:
        e = db.get(Experience, exp_id)
        if e is None:
            raise HTTPException(status_code=404, detail="经验不存在")
        if req.status not in ("confirmed", "discarded", "draft", "pending"):
            raise HTTPException(status_code=400, detail="非法状态")
        e.status = req.status
        e.confirmed = req.status == "confirmed"
        if req.importance is not None:
            e.importance = req.importance
        db.commit()
        return {"ok": True, "status": e.status, "confirmed": e.confirmed}
    finally:
        db.close()


@router.delete("/api/experiences/{exp_id}")
def delete_experience(exp_id: int, request: Request):
    db = request.app.state.SessionLocal()
    try:
        e = db.get(Experience, exp_id)
        if e is None:
            raise HTTPException(status_code=404, detail="经验不存在")
        db.delete(e)
        db.commit()
        vectors = request.app.state.vectors
        if vectors is not None:
            vectors.delete_entity("experience", exp_id)
        return {"ok": True}
    finally:
        db.close()


# ---------- 任务面板 ----------

@router.get("/api/tasks")
def list_tasks(request: Request, limit: int = 20):
    db = request.app.state.SessionLocal()
    try:
        rows = (
            db.query(Task)
            .order_by(Task.updated_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "tasks": [
                {
                    "id": t.id,
                    "task_name": t.task_name,
                    "goal": t.goal,
                    "stage": t.stage,
                    "status": t.status,
                    "current_problem": t.current_problem,
                    "related_files": t.related_files,
                    "project": t.project,
                    "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                }
                for t in rows
            ]
        }
    finally:
        db.close()


class TaskUpdate(BaseModel):
    stage: str | None = None
    task_name: str | None = None
    current_problem: str | None = None


@router.patch("/api/tasks/{task_id}")
def update_task(task_id: int, req: TaskUpdate, request: Request):
    """用户手动修正任务状态（如标记完成/放弃）。"""
    db = request.app.state.SessionLocal()
    try:
        t = db.get(Task, task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if req.stage:
            if req.stage not in ("planning", "working", "blocked", "testing", "solved", "abandoned"):
                raise HTTPException(status_code=400, detail="非法阶段")
            t.stage = req.stage
            if req.stage in ("solved", "abandoned"):
                t.status = "closed"
                from datetime import datetime
                t.closed_at = datetime.now()
        if req.task_name:
            t.task_name = req.task_name[:100]
        if req.current_problem is not None:
            t.current_problem = req.current_problem or None
        db.commit()
        return {"ok": True, "stage": t.stage}
    finally:
        db.close()


# ---------- 通知中心 ----------

@router.get("/api/notifications")
def list_notifications(request: Request, limit: int = 50):
    notify = getattr(request.app.state, "notify", None)
    if notify is None:
        return {"notifications": []}
    return {"notifications": notify.list_pending(limit=limit)}


@router.get("/api/notifications/count")
def notification_count(request: Request):
    notify = getattr(request.app.state, "notify", None)
    if notify is None:
        return {"pending": 0}
    return {"pending": len(notify.list_pending(limit=500))}


class ResolveRequest(BaseModel):
    status: str  # done / dismissed


@router.post("/api/notifications/{notif_id}/resolve")
def resolve_notification(notif_id: int, req: ResolveRequest, request: Request):
    notify = getattr(request.app.state, "notify", None)
    if notify is None:
        raise HTTPException(status_code=404, detail="通知服务不可用")
    ok = notify.resolve(notif_id, req.status)
    if not ok:
        raise HTTPException(status_code=404, detail="通知不存在")
    return {"ok": True}


# ---------- 记忆维护 ----------

@router.get("/api/maintenance")
def maintenance_stats(request: Request):
    con = getattr(request.app.state, "consolidation", None)
    if con is None:
        return {"enabled": False}
    return {"enabled": True, **con.stats()}


class RunMaintenance(BaseModel):
    job: str = "daily"  # hourly | episodic | daily


@router.post("/api/maintenance/run")
def run_maintenance(req: RunMaintenance, request: Request):
    con = getattr(request.app.state, "consolidation", None)
    if con is None:
        raise HTTPException(status_code=503, detail="巩固服务未启用")
    if req.job == "hourly":
        stats = con.hourly()
    elif req.job == "episodic":
        stats = con.episodic()
    else:
        stats = con.daily()
    return {"ok": True, "stats": stats}


# ---------- 采集控制 ----------

class PauseRequest(BaseModel):
    paused: bool


@router.post("/api/collector/pause")
def pause_collector(req: PauseRequest, request: Request):
    request.app.state.collector_paused = req.paused
    # 调度级暂停：任务直接不执行，不再空跑刷日志
    monitor = getattr(request.app.state, "monitor", None)
    if monitor is not None:
        monitor.set_paused(req.paused)
    # 采集暂停 → 任务引擎自动分析同步停摆（聊天/手动信号不受影响）
    task_engine = getattr(request.app.state, "task_engine", None)
    if task_engine is not None:
        try:
            task_engine.set_paused(req.paused)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "paused": req.paused}


# ---------- 对话反馈 ----------

class FeedbackRequest(BaseModel):
    message_id: str
    useful: bool


@router.post("/api/feedback")
def submit_feedback(req: FeedbackRequest, request: Request):
    db = request.app.state.SessionLocal()
    try:
        m = db.query(ChatMessage).filter(ChatMessage.message_id == req.message_id).first()
        if m is None:
            raise HTTPException(status_code=404, detail="消息不存在")
        db.add(
            UserSignal(
                kind="rate",
                target_type="chat",
                target_id=m.id,
                value=1.0 if req.useful else 0.0,
                note="useful" if req.useful else "useless",
            )
        )
        db.commit()
        return {"ok": True}
    finally:
        db.close()
