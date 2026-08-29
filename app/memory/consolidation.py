"""记忆巩固流水线（模拟睡眠记忆巩固）。

原则："遗忘 = 降级，不是删除"——AI 只做重要性排序，真正的删除由
确定性保留策略 + 回收站决定。

- hourly：保留上限裁剪
- episodic（每 2h）：重要性重算 + 高价值事件批量摘要 → 情景记忆
- daily：短期日志/事件超期降级（先年度摘要再进回收站）、回收站清理、
        待确认经验超时转草稿、每日经验候选、重要性重算
- 每次运行写入 ConsolidationLog（可观测性）
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func

from app.core.models import (ConsolidationLog, Event, Experience, MemoryShort,
                             Notification, Task, Trash)
from app.memory.scoring import get_weights, score_importance

log = logging.getLogger("mindtrace.memory.consolidation")


class ConsolidationService:
    def __init__(self, cfg, session_factory, vectors=None, llm=None, memory=None, notify=None,
                 graph_store=None):
        self.cfg = cfg
        self.session_factory = session_factory
        self.vectors = vectors
        self.llm = llm
        self.memory = memory
        self.notify = notify
        self.graph_store = graph_store

    # ---------- 对外入口 ----------

    def hourly(self) -> dict:
        """每小时：上限裁剪。"""
        stats = {"trimmed": 0}
        caps = self.cfg.caps or {}
        db = self.session_factory()
        try:
            for model, cap in (("memories_short", caps.get("memories_short", 50000)),
                               ("events", caps.get("events", 200000)),
                               ("experiences", caps.get("experiences", 5000))):
                cls = {"memories_short": MemoryShort, "events": Event, "experiences": Experience}[model]
                total = db.query(func.count(cls.id)).scalar() or 0
                if total <= cap:
                    continue
                rows = db.query(cls).order_by(cls.importance.asc(), cls.id.asc()).limit(total - cap).all()
                for r in rows:
                    self._to_trash(db, model, r)
                stats["trimmed"] += total - cap
            db.commit()
        finally:
            db.close()
        self._log("hourly", stats)
        return stats

    def episodic(self) -> dict:
        """每 2 小时：高价值事件批量摘要 → 情景记忆（防漂移：只摘要，不改原文）。"""
        stats = {"summarized": 0}
        if self.llm is None or not self.llm.ready:
            return stats
        db = self.session_factory()
        try:
            # 最近 2h 内重要事件（重要性 ≥0.55）
            since = datetime.now() - timedelta(hours=2)
            rows = (
                db.query(Event)
                .filter(Event.time >= since, Event.importance >= 0.55, Event.source != "episodic_summary")
                .order_by(Event.time.desc())
                .limit(20)
                .all()
            )
            if len(rows) < 5:
                return stats
            lines = "\n".join(f"- [{e.time:%m-%d %H:%M}] {e.activity[:100]}" for e in rows)
            prompt = (
                "把以下一段时间的重要工作经历压缩为一段 100 字以内的情景摘要"
                "（保留关键动作、成果、时间脉络）。只输出摘要。\n\n" + lines
            )
            try:
                db.commit()
                summary = self.llm.chat(
                    [{"role": "system", "content": "你是记忆巩固助手。"},
                     {"role": "user", "content": prompt}],
                    temperature=0.2,
                ).strip()[:300]
            except Exception as exc:  # noqa: BLE001
                log.warning("情景摘要失败: %s", exc)
                return stats
            if summary:
                db.add(Event(time=datetime.now(), app="mindtrace",
                             activity=f"情景摘要：{summary}", intent=summary,
                             importance=0.6, source="episodic_summary"))
                db.commit()
                stats["summarized"] = 1
                log.info("情景记忆：%s", summary[:40])
        finally:
            db.close()
        self._log("episodic", stats)
        return stats

    def daily(self) -> dict:
        """每天：超期降级（先年度摘要）、回收站清理、待确认超时草稿、每日经验候选、重要性重算。"""
        stats = {"expired_short": 0, "expired_events": 0, "annual_summaries": 0,
                 "trash_purged": 0, "drafts": 0, "candidates": 0, "rescored": 0,
                 "graph_merged": 0}
        db = self.session_factory()
        now = datetime.now()
        try:
            # 1. 短期日志超期 → 回收站
            short_deadline = now - timedelta(hours=self.cfg.short_retention_hours)
            for r in db.query(MemoryShort).filter(MemoryShort.time < short_deadline).limit(2000).all():
                self._to_trash(db, "memories_short", r)
                stats["expired_short"] += 1
            # 2. 事件超保留期 → 先年度摘要，再进回收站
            ev_deadline = now - timedelta(days=self.cfg.event_retention_days)
            old_events = db.query(Event).filter(
                Event.time < ev_deadline, Event.source != "annual_summary"
            ).order_by(Event.time.asc()).limit(2000).all()
            if old_events:
                stats["annual_summaries"] = self._annual_summaries(db, old_events)
                for r in old_events:
                    self._to_trash(db, "events", r)
                    stats["expired_events"] += 1
            # 3. 回收站超期 → 永久删除
            purge_deadline = now - timedelta(days=self.cfg.trash_days)
            for t in db.query(Trash).filter(Trash.expires_at < purge_deadline).all():
                db.delete(t)
                stats["trash_purged"] += 1
            # 4. 待确认经验超时（>24h 未处理）→ 低优先级草稿（不丢数据）
            exp_deadline = now - timedelta(hours=24)
            for e in db.query(Experience).filter(
                Experience.status == "pending", Experience.created_at < exp_deadline
            ).limit(500).all():
                e.status = "draft"
                e.importance = round(e.importance * 0.7, 3)
                stats["drafts"] += 1
            # 5. 每日经验候选（重要事件 → LLM 提炼可复用经验候选 → 通知确认）
            if self.llm is not None and self.llm.ready:
                stats["candidates"] = self._daily_candidates(db, now)
            # 6. 重要性重算（完整 w1-w6 公式 + 任务相关性）
            active_projects = {t.project for t in db.query(Task).filter(Task.status == "active").all() if t.project}
            weights = get_weights(db)
            for r in db.query(Event).filter(Event.importance >= 0.3).limit(2000).all():
                rel = 0.3 if (r.project and r.project in active_projects) else 0.0
                new_score = score_importance(
                    r.importance, r.created_at, now,
                    ref_count=getattr(r, "ref_count", 0) or 0,
                    task_relevant=rel,
                    weights=weights,
                )
                if abs(new_score - r.importance) > 0.01:
                    r.importance = new_score
                    stats["rescored"] += 1
            # 7. 图谱：合并重复节点 + w6 图引用接入经验 ref_count
            if self.graph_store is not None:
                stats["graph_merged"] = self.graph_store.merge_duplicates()
                self._sync_graph_refs(db)
            db.commit()
        finally:
            db.close()
        self._log("daily", stats)
        return stats

    # ---------- 内部 ----------

    def _annual_summaries(self, db, old_events: list[Event]) -> int:
        """按 年+项目 分组，LLM 生成年度摘要事件，再降级原文。"""
        if self.llm is None or not self.llm.ready:
            return 0
        groups: dict[tuple, list[Event]] = {}
        for e in old_events:
            try:
                year = e.time.year if e.time else now_year()
            except Exception:
                year = now_year()
            groups.setdefault((year, e.project or "default"), []).append(e)
        count = 0
        for (year, project), evs in list(groups.items())[:5]:
            lines = "\n".join(f"- [{e.time:%m-%d}] {e.activity[:90]}" for e in evs[:30])
            prompt = (
                f"把 {year} 年项目 {project} 的经历压缩为一段 150 字以内的年度摘要"
                "（关键成果/里程碑/难点）。只输出摘要。\n\n" + lines
            )
            try:
                db.commit()
                summary = self.llm.chat(
                    [{"role": "system", "content": "你是记忆巩固助手。"},
                     {"role": "user", "content": prompt}],
                    temperature=0.2,
                ).strip()[:400]
            except Exception:  # noqa: BLE001
                continue
            if summary:
                db.add(Event(time=datetime(year, 12, 31), app="mindtrace",
                             activity=f"年度摘要({project} {year})：{summary}",
                             intent=summary, importance=0.7,
                             project=project, source="annual_summary"))
                count += 1
        return count

    def _daily_candidates(self, db, now: datetime) -> int:
        """从当天高重要事件提炼可复用经验候选 → 待确认通知。"""
        since = now - timedelta(days=1)
        rows = (
            db.query(Event)
            .filter(Event.time >= since, Event.importance >= 0.6, Event.source != "episodic_summary")
            .order_by(Event.time.desc())
            .limit(15)
            .all()
        )
        if len(rows) < 3:
            return 0
        lines = "\n".join(f"- {e.activity[:120]}" for e in rows)
        prompt = (
            "从下面这些经历中判断：是否存在**可复用的经验**（解决过的问题/踩过的坑/有效方法）。"
            "如果有，输出 JSON：{\"candidates\": [{\"problem\": \"问题\", \"solution\": \"方案\"}]}，"
            "最多 3 条；没有则输出 {\"candidates\": []}。只输出 JSON。\n\n" + lines
        )
        try:
            db.commit()
            raw = self.llm.chat(
                [{"role": "system", "content": "你是经验提炼专家，只输出合法 JSON。"},
                 {"role": "user", "content": prompt}],
                temperature=0.3,
            )
            data = self._parse_json(raw)
        except Exception as exc:  # noqa: BLE001
            log.debug("经验候选提炼失败: %s", exc)
            return 0
        candidates = data.get("candidates") or []
        count = 0
        for c in candidates[:3]:
            problem = str(c.get("problem", "")).strip()
            solution = str(c.get("solution", "")).strip()
            if not problem:
                continue
            key = f"candidate:{problem[:40]}"
            exists = db.query(Notification).filter(Notification.kind == "daily_candidate").count() > 50
            # 简单去重：同问题已存在经验则跳过
            if db.query(Experience).filter(Experience.problem == problem).first():
                continue
            if self.notify is not None:
                self.notify.notify(
                    kind="daily_candidate",
                    title="📋 经验候选",
                    body=f"从今天的经历中提炼：{problem}" + (f" → {solution}" if solution else ""),
                    action={"type": "candidate", "problem": problem, "solution": solution},
                )
            count += 1
        return count

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text
            text = text.strip().lstrip("json").strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                    return data if isinstance(data, dict) else {}
                except json.JSONDecodeError:
                    return {}
            return {}

    def _to_trash(self, db, model: str, row) -> None:
        snapshot = {
            "model": model,
            "id": row.id,
            "time": row.time.isoformat() if getattr(row, "time", None) else None,
            "text": getattr(row, "activity", None) or getattr(row, "text", None) or str(getattr(row, "problem", ""))[:300],
            "importance": getattr(row, "importance", 0.5),
        }
        expires = datetime.now() + timedelta(days=self.cfg.trash_days)
        db.add(Trash(target_type=model, target_id=row.id, payload=snapshot, expires_at=expires))
        if self.vectors is not None and model in ("events", "experiences"):
            try:
                self.vectors.delete_entity(model.rstrip("s"), row.id)
            except Exception:  # noqa: BLE001
                pass
        db.delete(row)

    def _sync_graph_refs(self, db) -> None:
        """w6：图谱中被引用次数（经验节点的边数）→ Experience.ref_count。"""
        if self.graph_store is None:
            return
        try:
            for nid, data in list(self.graph_store.graph.nodes(data=True)):
                if data.get("type") != "experience":
                    continue
                meta = data.get("meta") or {}
                exp_id = meta.get("exp_id")
                if not exp_id:
                    continue
                exp = db.get(Experience, int(exp_id))
                if exp is not None:
                    exp.ref_count = self.graph_store.count_edges_for_node(nid)
        except Exception as exc:  # noqa: BLE001
            log.debug("图引用同步失败: %s", exc)

    def _log(self, job: str, stats: dict) -> None:
        db = self.session_factory()
        try:
            db.add(ConsolidationLog(job=job, stats=stats))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("运行记录失败: %s", exc)
        finally:
            db.close()

    def stats(self) -> dict:
        """维护视图数据。"""
        db = self.session_factory()
        try:
            runs = (
                db.query(ConsolidationLog)
                .order_by(ConsolidationLog.id.desc())
                .limit(10)
                .all()
            )
            return {
                "counts": {
                    "memories_short": db.query(func.count(MemoryShort.id)).scalar() or 0,
                    "events": db.query(func.count(Event.id)).scalar() or 0,
                    "experiences": db.query(func.count(Experience.id)).scalar() or 0,
                    "trash": db.query(func.count(Trash.id)).scalar() or 0,
                },
                "caps": self.cfg.caps or {},
                "retention": {
                    "short_hours": self.cfg.short_retention_hours,
                    "events_days": self.cfg.event_retention_days,
                    "trash_days": self.cfg.trash_days,
                },
                "runs": [
                    {"job": r.job, "stats": r.stats, "run_at": r.run_at.isoformat() if r.run_at else None}
                    for r in runs
                ],
            }
        finally:
            db.close()


def now_year() -> int:
    return datetime.now().year
