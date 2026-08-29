"""知识图谱构建：从事件 / 经验 / 任务 增量建图。

节点：项目/文件/代码/论文/问题/方案/决策/经验
边：修改/查看/来源/导致/失败/成功/替代/复用（带时间+置信度）
增量：按上次同步 id 推进（Setting 表记录游标）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.models import Event, Experience, Task
from app.core.settings_store import get_int, set_setting
from app.graph.store import GraphStore

log = logging.getLogger("mindtrace.graph.builder")

PROBLEM_KW = ("报错", "错误", "异常", "失败", "error", "failed", "exception", "bug", "卡住", "不行")
SUCCESS_KW = ("解决", "搞定", "完成", "通过", "成功", "fixed", "works", "success")


class GraphBuilder:
    def __init__(self, session_factory, store: GraphStore):
        self.session_factory = session_factory
        self.store = store

    # ---------- 增量同步 ----------

    def sync_all(self) -> dict:
        db = self.session_factory()
        try:
            last_event = get_int(db, "graph_last_event_id", 0)
            last_exp = get_int(db, "graph_last_exp_id", 0)
            last_task = get_int(db, "graph_last_task_id", 0)

            # 游标自愈：库里有数据但图谱是空的，说明游标超前（数据被清理/删除过）——
            # 重置游标重新全量同步，避免"游标>最大id"导致永远跳过。
            if self.store.counts()["nodes"] == 0 and (last_event > 0 or last_exp > 0 or last_task > 0):
                db.execute("DELETE FROM settings WHERE key IN "
                           "('graph_last_event_id','graph_last_exp_id','graph_last_task_id')")
                db.commit()
                last_event = last_exp = last_task = 0
                log.info("图谱游标自愈：重置为 0 全量重建")

            events = db.query(Event).filter(Event.id > last_event).order_by(Event.id).limit(500).all()
            exps = db.query(Experience).filter(Experience.id > last_exp).order_by(Experience.id).limit(200).all()
            tasks = db.query(Task).filter(Task.id > last_task).order_by(Task.id).limit(200).all()

            stats = {"events": 0, "experiences": 0, "tasks": 0}
            for ev in events:
                self.build_from_event(ev)
                stats["events"] += 1
            for e in exps:
                self.build_from_experience(e)
                stats["experiences"] += 1
            for t in tasks:
                self.build_from_task(t)
                stats["tasks"] += 1

            if events:
                set_setting(db, "graph_last_event_id", str(events[-1].id))
            if exps:
                set_setting(db, "graph_last_exp_id", str(exps[-1].id))
            if tasks:
                set_setting(db, "graph_last_task_id", str(tasks[-1].id))
        finally:
            db.close()
        if any(stats.values()):
            log.info("图谱增量：%s", stats)
        return stats

    # ---------- 构建规则 ----------

    def build_from_event(self, ev: Event) -> None:
        project_id = None
        if ev.project:
            project_id = self.store.get_or_create_node("project", ev.project, time=ev.time)

        text = f"{ev.activity or ''} {ev.intent or ''}".strip()

        # 文件节点（activity: "modified文件：path"）
        if ev.source == "file" and ev.activity:
            path_part = ev.activity.split("：")[-1].strip() if "：" in ev.activity else ev.activity.strip()
            try:
                fname = Path(path_part).name
            except Exception:
                fname = None
            if fname and "." in fname:
                fid = self.store.get_or_create_node("file", fname, summary=path_part[:300], time=ev.time)
                kind = ev.activity.split("：")[0] if "：" in ev.activity else "modified"
                relation = "修改" if "modif" in kind or "修改" in kind else "查看"
                if project_id:
                    self.store.add_edge(fid, project_id, relation, time=ev.time, confidence=0.9)

        # 文档/论文（document 源）
        if ev.source == "document" and ev.activity:
            title = (ev.activity or "").split("：")[-1].strip()[:60]
            if title:
                did = self.store.get_or_create_node("paper", title, summary=text[:200], time=ev.time)
                if project_id:
                    self.store.add_edge(did, project_id, "来源", time=ev.time, confidence=0.7)

        # 问题 / 成功信号
        low = text.lower()
        if any(k in low for k in PROBLEM_KW):
            pid = self.store.get_or_create_node("problem", text[:60] or "未知问题",
                                                summary=text[:200], time=ev.time)
            if project_id:
                self.store.add_edge(project_id, pid, "导致", time=ev.time, confidence=0.7)
        if any(k in low for k in SUCCESS_KW):
            sid = self.store.get_or_create_node("solution", text[:60] or "已解决",
                                                summary=text[:200], time=ev.time)
            if project_id:
                self.store.add_edge(project_id, sid, "成功", time=ev.time, confidence=0.7)

    def build_from_experience(self, exp: Experience) -> None:
        t = exp.created_at
        prob = self.store.get_or_create_node("problem", (exp.problem or "")[:60],
                                             summary=exp.problem, time=t)
        sol = self.store.get_or_create_node("solution", (exp.final_solution or exp.problem or "")[:60],
                                            summary=exp.final_solution or exp.problem, time=t)
        exp_node = self.store.get_or_create_node("experience", (exp.problem or "")[:60],
                                                 summary=exp.problem, meta={"exp_id": exp.id}, time=t)
        # 问题 → 最终方案（成功）
        self.store.add_edge(prob, sol, "成功", time=t, confidence=0.9)
        # 方案 → 经验（来源）
        self.store.add_edge(sol, exp_node, "来源", time=t, confidence=0.9)
        # 经验 → 问题（复用）
        self.store.add_edge(exp_node, prob, "复用", time=t, confidence=0.9)
        # 失败方案 → 问题（失败）与最终方案的替代关系
        for fs in (exp.failed_solutions or [])[:5]:
            fname = str(fs)[:60]
            if not fname:
                continue
            fid = self.store.get_or_create_node("solution", fname, summary=str(fs)[:200], time=t)
            self.store.add_edge(prob, fid, "失败", time=t, confidence=0.85)
            if str(fs) != (exp.final_solution or ""):
                self.store.add_edge(fid, sol, "替代", time=t, confidence=0.8)
        # 项目链接
        if exp.tags:
            for tag in (exp.tags or [])[:3]:
                self.store.get_or_create_node("project", str(tag), time=t)

    def build_from_task(self, task: Task) -> None:
        t = task.updated_at or task.created_at
        decision = self.store.get_or_create_node(
            "decision", (task.task_name or "")[:60],
            summary=f"{task.goal or ''} | {task.current_problem or ''}", time=t,
        )
        if task.project:
            pid = self.store.get_or_create_node("project", task.project, time=t)
            self.store.add_edge(pid, decision, "导致", time=t, confidence=0.8)
        if task.current_problem:
            prob = self.store.get_or_create_node("problem", task.current_problem[:60],
                                                 summary=task.current_problem, time=t)
            self.store.add_edge(decision, prob, "导致", time=t, confidence=0.8)
        if task.solutions:
            for s in (task.solutions or [])[-3:]:
                sol = self.store.get_or_create_node("solution", str(s)[:60], summary=str(s)[:200], time=t)
                self.store.add_edge(decision, sol, "成功", time=t, confidence=0.85)
