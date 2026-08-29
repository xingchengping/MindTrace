"""Task Intelligence Engine：任务状态机 + 卡点/攻克检测 + 自动经验沉淀。

状态：planning → working → blocked ⇄ testing → solved / abandoned
攻克判定：多信号投票 + 迟滞（≥N 个独立信号才判 solved，单次波动不触发）。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.models import ChatSession, Event, Experience, Reminder, Task
from app.tasks.session import add_bullets, compact_via_llm, get_or_create_agent_session

log = logging.getLogger("mindtrace.tasks")

PROBLEM_KW = ("报错", "错误", "异常", "失败", "error", "failed", "exception", "bug", "卡住", "不行", "崩溃", "cannot", "can't", "挂掉")
SUCCESS_KW = ("解决", "搞定", "完成", "通过", "成功", "fixed", "works", "success", "passed", "done", "修好了")
DONE_PHRASES = ("搞定了", "完成了", "解决了", "修好了", "通过了", "已解决", "done", "fixed")
STUCK_PHRASES = ("卡住", "卡在", "搞不定", "搞不定", "一直失败", "报错", "无法解决", "stuck")

STAGES = ("planning", "working", "blocked", "testing", "solved", "abandoned")


class TaskEngine:
    def __init__(self, cfg, session_factory, memory, llm, notify):
        self.cfg = cfg
        self.session_factory = session_factory
        self.memory = memory
        self.llm = llm
        self.notify = notify
        self.scheduler: BackgroundScheduler | None = None
        self._fail_streak: dict[int, int] = {}   # task_id -> 连续失败批次数
        self._last_problem: dict[int, str] = {}
        self._recall_minutes = int(getattr(cfg, "recall_minutes", 15) or 15)
        self._recall_threshold = float(getattr(cfg, "recall_threshold", 0.85) or 0.85)
        self._last_context_refine: float = 0.0
        self._paused = False  # 采集暂停时自动分析停摆（聊天/手动信号不受影响）

    # ---------- 采集暂停联动 ----------

    def set_paused(self, paused: bool) -> None:
        """采集暂停 → 停止基于事件的自动任务分析（process_window 直接跳过）。

        用户手动确认信号（on_user_message 的 完成/卡住）不受影响。
        """
        self._paused = bool(paused)
        log.info("Task Engine 自动分析%s", "暂停" if paused else "恢复")

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self.llm is None or not self.llm.ready:
            log.warning("LLM 未就绪，Task Engine 延迟启动（等待就绪后由首次对话触发）")
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self.scheduler.add_job(
            self.process_window, "interval", minutes=max(self.cfg.batch_minutes, 2),
            id="task_engine", max_instances=1,
        )
        self.scheduler.start()
        log.info("Task Engine 已启动（每 %d 分钟分析一次）", self.cfg.batch_minutes)

    def stop(self) -> None:
        if self.scheduler and self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        self.scheduler = None

    # ---------- 任务查询 ----------

    def get_active_task(self, db, project: str | None = None) -> Task | None:
        q = db.query(Task).filter(Task.status == "active")
        if project:
            q = q.filter(Task.project == project)
        return q.order_by(Task.updated_at.desc()).first()

    def _ensure_task(self, db, project: str | None, hint: str | None = None) -> Task:
        task = self.get_active_task(db, project)
        if task is None:
            name = hint or (project or "未命名任务")[:60]
            task = Task(task_name=name, project=project, stage="working",
                        status="active", context={"focus": hint or ""})
            db.add(task)
            db.flush()
        return task

    # ---------- 外部信号 ----------

    def on_user_message(self, text: str) -> None:
        """聊天中的显式完成/卡住信号（用户确认，最高权重信号）。"""
        db = self.session_factory()
        try:
            task = self.get_active_task(db)
            if task is None:
                return
            if any(p in text for p in DONE_PHRASES):
                task.stage = "solved"
                db.commit()
                self._on_solved(task)
            elif any(p in text for p in STUCK_PHRASES):
                task.stage = "blocked"
                task.current_problem = task.current_problem or text[:120]
                db.commit()
        finally:
            db.close()

    # ---------- 窗口分析 ----------

    def process_window(self) -> None:
        """分析最近一批事件 + Agent 会话上下文，更新任务状态。"""
        if self._paused:
            log.debug("采集已暂停，跳过任务自动分析")
            return
        if self.llm is None or not self.llm.ready:
            return
        db = self.session_factory()
        try:
            recent = (
                db.query(Event)
                .order_by(Event.id.desc())
                .limit(30)
                .all()
            )
            if not recent:
                return
            # 按项目聚合
            by_project: dict[str, list[Event]] = {}
            for ev in recent:
                key = ev.project or "default"
                by_project.setdefault(key, []).append(ev)

            for project, events in by_project.items():
                task = self._ensure_task(db, project)
                self._update_agent_session(db, task, events)
                self._update_task_state(db, task, events)
            db.commit()
        finally:
            db.close()

    def _update_agent_session(self, db, task: Task, events: list[Event]) -> None:
        """把 Agent 相关事件追加为会话上下文条目（LSM）。"""
        agent_events = [e for e in events if e.source in ("agent_chat", "collector_batch") or (e.app and "code" in e.app.lower())]
        if not agent_events:
            return
        session = get_or_create_agent_session(db, "agent", task.project)
        bullets = [f"{e.time:%H:%M} {e.activity}" for e in agent_events[:10]]

        def _compact(old: list[str]) -> str:
            db.commit()  # LLM 调用前提交事务，避免长事务持锁
            return compact_via_llm(self.llm, old)

        add_bullets(
            db, session, bullets,
            cap=self.cfg.focus_bullet_cap,
            compact_batch=self.cfg.compaction_batch,
            compact_fn=_compact,
        )
        # 会话级 current_problem / current_focus 同步
        if task.current_problem:
            session.current_problem = task.current_problem
        session.current_focus = (task.context or {}).get("focus") or task.task_name
        db.add(session)
        # LLM 增量提炼：goal / focus / problem / attempts（5 分钟节流）
        self._refine_session_context(db, task, session)

    def _refine_session_context(self, db, task: Task, session) -> None:
        """从会话条目提炼目标/重点/卡点/尝试（LSM 增量理解的核心，只输出增量）。"""
        now = time.time()
        if now - self._last_context_refine < 300:
            return
        bullets = list(session.recent_bullets or [])[-15:]
        if len(bullets) < 3:
            return
        lines = "\n".join(f"- {b}" for b in bullets)
        prompt = (
            "根据以下工作过程记录，提炼当前任务理解（只输出变化/最新信息）：\n"
            "JSON 字段：goal(当前目标), focus(当前重点，一句话), "
            "problem(当前卡点或null), attempts(已尝试方案数组，最多5个，仅新的)。\n"
            "只输出 JSON。\n\n记录：\n" + lines
        )
        try:
            db.commit()
            raw = self.llm.chat(
                [{"role": "system", "content": "你是任务理解提炼器，只输出合法 JSON。"},
                 {"role": "user", "content": prompt}],
                temperature=0.2,
            )
            data = self._parse_json(raw)
        except Exception as exc:  # noqa: BLE001
            log.debug("上下文提炼失败: %s", exc)
            return
        if not data:
            return
        changed = False
        if data.get("goal"):
            session.goal = str(data["goal"])[:300]
            task.goal = session.goal
            changed = True
        if data.get("focus"):
            session.current_focus = str(data["focus"])[:200]
            ctx = dict(task.context or {})
            ctx["focus"] = session.current_focus
            task.context = ctx
            changed = True
        if data.get("problem"):
            session.current_problem = str(data["problem"])[:300]
            changed = True
        if data.get("attempts") and isinstance(data["attempts"], list):
            attempts = list(task.attempt_history or [])
            for a in data["attempts"]:
                s = str(a)[:120]
                if s and s not in attempts:
                    attempts.append(s)
            task.attempt_history = attempts[-30:]
            changed = True
        if changed:
            db.add(session)
            db.commit()
            self._last_context_refine = now
            log.info("[task %s] 上下文提炼：goal=%s focus=%s", task.id,
                     (session.goal or "")[:30], (session.current_focus or "")[:30])

    def _update_task_state(self, db, task: Task, events: list[Event]) -> None:
        if task.stage in ("solved", "abandoned"):
            return
        text = " ".join((e.activity or "") + " " + (e.intent or "") for e in events)
        low = text.lower()
        # 失败信号只看最新事件（旧失败不阻塞"错误消失"判定）
        latest = events[0] if events else None
        latest_text = ((latest.activity or "") + " " + (latest.intent or "")).lower() if latest else ""

        has_fail = any(k in latest_text for k in PROBLEM_KW)
        has_success = any(k in low for k in SUCCESS_KW)

        # ---- 卡点检测（连续 ≥N 批失败信号，迟滞）----
        streak = self._fail_streak.get(task.id, 0)
        streak = streak + 1 if has_fail else 0
        self._fail_streak[task.id] = streak

        if task.stage != "blocked" and streak >= self.cfg.blocked_persist_batches:
            db.commit()  # LLM 调用前提交，避免长事务持锁
            problem = self._refine_problem(task, text)
            task.stage = "blocked"
            task.current_problem = problem or text[:150]
            self._last_problem[task.id] = task.current_problem
            ctx = dict(task.context or {})
            ctx["blocked_since"] = datetime.now().isoformat()
            task.context = ctx
            log.info("[task %s] 检测到卡点：%s", task.id, task.current_problem[:50])
        elif task.stage == "blocked" and not has_fail and has_success:
            task.stage = "testing"
            task.current_problem = None if not task.current_problem else task.current_problem

        # ---- 实时联想提醒（v2.4）：卡点持续 ≥N 分钟 → 检索旧经验/事件 → 高相似弹通知 ----
        if task.stage == "blocked" and self.memory is not None and self.memory.enabled:
            self._maybe_recall_reminder(db, task)

        # ---- 攻克检测（多信号投票 + 迟滞）----
        signals = 0
        if has_success:
            signals += 1
        if self._last_problem.get(task.id) and not has_fail:  # 曾有卡点、本批无失败
            signals += 1
        if len(events) >= 2 and events[0].project and events[-1].project != events[0].project:
            signals += 1  # 焦点转移
        if task.stage == "testing":
            signals += 1
        # 信号③：文件停止变动或新产物出现
        ctx = dict(task.context or {})
        file_events = [e for e in events if e.source == "file"]
        last_file = ctx.get("last_file_activity")
        new_artifact = any((e.activity or "").startswith("created") for e in file_events)
        if file_events:
            ctx["last_file_activity"] = datetime.now().isoformat()
            task.context = ctx
        if new_artifact or (last_file is not None and not file_events):
            signals += 1  # 文件停止变动 / 新产物

        if signals >= self.cfg.solved_min_signals and task.stage not in ("solved", "abandoned", "planning"):
            task.stage = "solved"
            task.status = "active"
            db.commit()  # LLM 调用前提交
            log.info("[task %s] 攻克检测命中（信号=%d）", task.id, signals)
            self._on_solved(task)

        # ---- 常规推进 ----
        if task.stage in ("planning",) :
            task.stage = "working"
        # 焦点与尝试记录
        ctx = dict(task.context or {})
        focus = self._guess_focus(events)
        if focus:
            ctx["focus"] = focus
        task.context = ctx
        if task.task_name in ("未命名任务",) or not task.task_name:
            task.task_name = focus or "未命名任务"
        attempts = list(task.attempt_history or [])
        for e in events[:5]:
            act = (e.activity or "")[:80]
            if act and act not in attempts:
                attempts.append(act)
        task.attempt_history = attempts[-30:]

    # ---------- 攻克后处理 ----------

    @staticmethod
    def _as_list(v):
        """LLM 可能把数组编码成 JSON 字符串，统一规整为 list。"""
        if v is None:
            return None
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                try:
                    data = json.loads(s)
                    return [str(x) for x in data] if isinstance(data, list) else [s]
                except json.JSONDecodeError:
                    return [s]
            return [s] if s else None
        return None

    @staticmethod
    def _as_str(v):
        if v is None:
            return None
        if isinstance(v, list):
            return "；".join(str(x) for x in v)[:1000]
        return str(v)[:1000]

    def _on_solved(self, task: Task) -> None:
        """任务攻克 → 生成待确认经验 + 突破通知。"""
        if self.memory is None:
            return
        db = self.session_factory()
        try:
            bullets: list[str] = []
            session = None
            if task.project:
                from app.tasks.session import agent_client_id
                session = db.query(ChatSession).filter(ChatSession.client_id == agent_client_id("agent", task.project)).first()
            if session:
                bullets = list(session.recent_bullets or []) + list(session.summary_chunks or [])
            db.commit()  # 提交只读事务，再调用 LLM
            exp_data = self._generate_experience(task, bullets)
            # 兜底：LLM JSON 提取失败时，用任务字段生成最小经验，保证流程完整
            if not exp_data:
                exp_data = {
                    "problem": task.current_problem or task.task_name,
                    "final_solution": (task.solutions or [""])[-1] if task.solutions else None,
                    "background": f"来自任务：{task.task_name}",
                    "tags": [task.project] if task.project else None,
                }
            exp = Experience(
                problem=self._as_str(exp_data.get("problem")) or task.current_problem or task.task_name,
                background=self._as_str(exp_data.get("background")),
                attempts=self._as_list(exp_data.get("attempts")),
                failed_solutions=self._as_list(exp_data.get("failed_solutions")),
                final_solution=self._as_str(exp_data.get("final_solution")),
                scenarios=self._as_str(exp_data.get("scenarios")),
                advice=self._as_str(exp_data.get("advice")),
                tags=self._as_list(exp_data.get("tags")),
                importance=0.7,
                confirmed=False,
                status="pending",
                source_task_id=task.id,
            )
            db.add(exp)
            db.commit()
            db.refresh(exp)
            task.solutions = list(task.solutions or []) + [exp_data.get("final_solution", "")]
            task.status = "closed"
            task.closed_at = datetime.now()
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("经验生成失败: %s", exc)
            db.rollback()
            return
        finally:
            db.close()

        if self.notify is not None:
            self.notify.notify(
                kind="breakthrough",
                title="🎉 攻克确认",
                body=f"检测到任务「{task.task_name}」的难点已解决，是否沉淀为经验？",
                action={"type": "experience", "task_id": task.id},
            )

    def _generate_experience(self, task: Task, bullets: list[str]) -> dict:
        ctx_lines = "\n".join(f"- {b}" for b in bullets[-15:]) if bullets else "（无上下文记录）"
        prompt = (
            "根据以下任务信息与过程记录，提炼一条可复用的个人经验。\n"
            f"任务：{task.task_name}\n"
            f"目标：{task.goal or '未知'}\n"
            f"卡点：{task.current_problem or '未知'}\n"
            f"过程记录：\n{ctx_lines}\n\n"
            "输出 JSON，字段：problem(问题), background(背景), "
            "attempts(尝试过的方案数组), failed_solutions(失败方案数组), "
            "final_solution(最终解决方案), scenarios(适用场景), advice(未来建议), "
            "tags(标签数组)。只输出 JSON。"
        )
        try:
            raw = self.llm.chat(
                [{"role": "system", "content": "你是经验提炼专家，只输出合法 JSON。"},
                 {"role": "user", "content": prompt}],
                temperature=0.3,
            )
            return self._parse_json(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("经验提炼调用失败: %s", exc)
            return {}

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

    # ---------- 工具 ----------

    def _refine_problem(self, task: Task, text: str) -> str | None:
        try:
            prompt = (
                f"用户在项目 {task.task_name} 中遇到问题。根据最近的记录，用一句话概括当前卡点。\n"
                f"记录：{text[:300]}\n只输出卡点描述。"
            )
            return self.llm.chat(
                [{"role": "system", "content": "你擅长定位问题。"},
                 {"role": "user", "content": prompt}],
                temperature=0.2,
            ).strip()[:150] or None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _guess_focus(events: list[Event]) -> str | None:
        from collections import Counter
        c = Counter()
        for e in events:
            if e.app:
                c[e.app] += 1
            if e.project:
                c[e.project] += 0.5
        return c.most_common(1)[0][0] if c else None

    # ---------- 实时联想提醒 ----------

    def _maybe_recall_reminder(self, db, task: Task) -> None:
        """卡点持续 ≥N 分钟 → 向量检索旧经验/事件；相似度 ≥阈值 → 通知（同卡点只提醒一次）。"""
        if not task.current_problem:
            return
        ctx = dict(task.context or {})
        since_str = ctx.get("blocked_since")
        if not since_str:
            return
        try:
            since = datetime.fromisoformat(since_str)
        except ValueError:
            return
        if datetime.now() - since < timedelta(minutes=self._recall_minutes):
            return

        # 去重：同一问题指纹只提醒一次（Reminder 表）
        key = f"recall:{task.id}:{task.current_problem.strip()[:40]}"
        existing = db.query(Reminder).filter(
            Reminder.kind == "recall", Reminder.subject_key == key
        ).first()
        if existing is not None:
            return

        # 检索：当前卡点 vs 经验库 + 情景记忆
        snippets = self.memory.search(task.current_problem)
        if not snippets:
            return
        top = snippets[0]
        if top.get("score", 0) < self._recall_threshold:
            return

        db.add(Reminder(kind="recall", subject_key=key, target_id=task.id))
        db.commit()

        when = (top.get("time") or "")[:10]
        kind_label = "经验" if top.get("kind") == "experience" else "事件"
        if self.notify is not None:
            self.notify.notify(
                kind="recall_reminder",
                title="💡 你以前遇到过类似问题",
                body=f"「{task.current_problem[:50]}」与你 {when} 的{kind_label}相似（相似度 {top.get('score', 0):.2f}）：{top.get('text', '')[:60]}",
                action={"type": "recall", "task_id": task.id},
            )
