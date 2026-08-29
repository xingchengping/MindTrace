"""采集调度（APScheduler）：窗口轮询 / 文件监听 / 剪贴板 / 浏览器 / Git / 模型级批量提取。"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.collector.files import FileWatcher
from app.collector.pipeline import CapturePipeline
from app.collector.sources import browser_recent, clip_text, git_recent
from app.collector import window as win_capture
from app.core.models import MemoryShort

log = logging.getLogger("mindtrace.collector")

# 值得喂给 LLM 提取的来源。
# "window"（前台窗口标题）是多数用户最主要的活动来源：窗口切换产生的短时记忆
# 提取为事件后，重要性由 LLM 打分、巩固时按阈值筛选，噪音会被裁剪掉。
EXTRACT_SOURCES = ("agent_chat", "clipboard", "terminal", "file", "document", "git", "manual", "screenshot_hint", "window")


class MonitorService:
    def __init__(self, cfg, session_factory, memory, llm):
        self.cfg = cfg
        self.session_factory = session_factory
        self.memory = memory
        self.llm = llm
        self.pipeline = CapturePipeline(
            session_factory, memory, llm=llm,
            hot_keywords=tuple(cfg.hot_keywords),
            on_hot=self._hot_signal,
        )
        self.scheduler: BackgroundScheduler | None = None
        self.file_watcher: FileWatcher | None = None
        self._last_window: tuple[str, str, float] = ("", "", 0.0)
        self._last_clip = ""
        self._agent_buffer: list[dict] = []
        self._last_hot: float = 0.0          # 热信号即时批节流
        self._last_uia_hwnd = 0
        self._last_uia_hwnd_ts = 0.0
        self._last_uia_text = ""
        # 热更新开关（设置页/悬浮球修改，无需重启；初值来自 config）
        self.live_mode = cfg.monitor_mode
        self.live_pattern = cfg.locked_pattern
        self.live_clipboard = cfg.clipboard_enabled
        self.live_browser = cfg.browser_history
        self.live_git = cfg.git_enabled
        self.paused = False   # 采集暂停（调度级：暂停全部任务，不再空跑/刷日志）

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if not self.cfg.collector_enabled:
            log.info("采集已禁用（config collector.enabled=false）")
            return
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self.scheduler.add_job(
            self._poll_window, "interval", seconds=max(self.cfg.monitor_interval, 2),
            id="window", max_instances=1,
        )
        self.scheduler.add_job(self._poll_clipboard, "interval", seconds=5, id="clip", max_instances=1)
        self.scheduler.add_job(self._poll_uia, "interval", seconds=30, id="uia", max_instances=1)
        self.scheduler.add_job(self._poll_browser, "interval", seconds=300, id="browser", max_instances=1)
        if self.cfg.watch_dirs:
            self.scheduler.add_job(self._poll_git, "interval", seconds=600, id="git", max_instances=1)
            self.scheduler.add_job(self._poll_docs, "interval", seconds=600, id="docs", max_instances=1)
        self.scheduler.add_job(
            self._batch_extract, "interval", minutes=max(self.cfg.batch_minutes, 2),
            id="batch", max_instances=1,
        )
        self.scheduler.start()
        log.info("采集调度已启动（monitor=%s, 批量=%dmin）", self.live_mode, self.cfg.batch_minutes)
        # 初始即关闭窗口采集时，直接暂停窗口任务（避免空跑刷日志）
        if self.live_mode == "off":
            try:
                self.scheduler.pause_job("window")
            except Exception:  # noqa: BLE001
                pass

        if self.cfg.watch_dirs:
            self.file_watcher = FileWatcher(self.cfg.watch_dirs, self.pipeline.ingest)
            self.file_watcher.start()

    def stop(self) -> None:
        if self.file_watcher:
            self.file_watcher.stop()
            self.file_watcher = None
        if self.scheduler and self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass
        self.scheduler = None

    # ---------- 采集控制（调度级） ----------

    def set_paused(self, paused: bool) -> None:
        """暂停/恢复全部采集任务（任务直接不执行，不再空跑刷日志）。

        恢复时尊重"窗口采集已关闭"（live_mode=off）——窗口任务保持暂停。
        """
        self.paused = bool(paused)
        sched = self.scheduler
        if sched is None:
            return
        for job in sched.get_jobs():
            try:
                if self.paused:
                    job.pause()
                else:
                    if job.id == "window" and self.live_mode == "off":
                        continue  # 窗口采集本就关闭，保持暂停
                    job.resume()
            except Exception:  # noqa: BLE001
                pass
        log.info("采集已%s", "暂停" if self.paused else "恢复")

    def set_mode(self, mode: str, pattern: str = "") -> None:
        """热更新监控目标；关闭窗口采集时暂停窗口任务（不空跑刷日志）。"""
        self.live_mode = mode
        self.live_pattern = pattern
        sched = self.scheduler
        if sched is None or self.paused:
            return
        try:
            if mode == "off":
                sched.pause_job("window")
            else:
                sched.resume_job("window")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 采集回调 ----------

    def _is_agent_app(self, app: str) -> bool:
        if not app:
            return False
        low = app.lower()
        return any(a in low for a in self.cfg.agent_apps)

    def _poll_window(self) -> None:
        if self.live_mode == "off":
            return
        info = win_capture.get_foreground_window()
        if not info:
            return
        app, title = info["app"], info["title"]
        # 监控目标：follow=总是采集；locked=仅匹配锁定标题（热更新）
        if self.live_mode == "locked":
            if not (self.live_pattern and self.live_pattern.lower() in title.lower()):
                return
        now = time.time()
        if (app, title) == self._last_window[:2] and now - self._last_window[2] < 30:
            return  # 同一窗口 30s 内不重复
        self._last_window = (app, title, now)

        parsed = win_capture.parse_title(app, title)
        if self._is_agent_app(app):
            # Agent 对话窗口：进入会话缓冲，等待批量理解（不存原文）
            self._agent_buffer.append({"app": app, "title": title, "time": now})
            if len(self._agent_buffer) > 50:
                self._agent_buffer = self._agent_buffer[-50:]
            parsed["source"] = "agent_chat"
            parsed["text"] = f"在 {app} 中：{title}"
        else:
            parsed["source"] = "window"
        parsed["time"] = datetime.fromtimestamp(now)
        self.pipeline.ingest(parsed)

    def _poll_clipboard(self) -> None:
        if not self.live_clipboard:
            return
        text = clip_text()
        if not text or text == self._last_clip:
            return
        self._last_clip = text
        # 只采集有意义长度的文本
        if len(text.strip()) < 12 or len(text) > 5000:
            return
        # 前台为 Agent 时，剪贴板内容视为 Agent 对话片段
        info = win_capture.get_foreground_window()
        if info and self._is_agent_app(info.get("app", "")):
            self.pipeline.ingest({"source": "agent_chat", "app": info["app"], "text": text, "time": datetime.now()})
        else:
            self.pipeline.ingest({"source": "clipboard", "app": info.get("app") if info else None, "text": text, "time": datetime.now()})

    def _poll_browser(self) -> None:
        if not self.live_browser:
            return
        for browser in ("edge", "chrome"):
            rows = browser_recent(browser, limit=10)
            for r in rows:
                self.pipeline.ingest({"source": "browser", "app": browser, "title": r["title"], "url": r["url"], "time": r["time"]})

    def _poll_git(self) -> None:
        if not self.live_git:
            return
        for d in self.cfg.watch_dirs:
            for c in git_recent(d, limit=5):
                self.pipeline.ingest({"source": "git", "app": "git", "subject": c["subject"], "dir": d, "time": datetime.now()})

    # ---------- UIA / 终端 / Agent 正文 ----------

    def _poll_uia(self) -> None:
        """读取前台窗口的 UIAutomation 文本（终端日志 / Agent 对话正文）。"""
        if self.llm is None or not self.llm.ready:
            return
        from app.collector import uia
        if not uia.available():
            return
        info = win_capture.get_foreground_window()
        if not info:
            return
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
        except Exception:
            hwnd = 0
        app_low = (info.get("app") or "").lower()
        is_target = self._is_agent_app(info.get("app", "")) or any(
            k in app_low for k in ("cmd", "powershell", "terminal", "conhost", "windowsterminal")
        )
        if not is_target and not self._last_uia_hwnd:
            return
        if hwnd == self._last_uia_hwnd and time.time() - self._last_uia_hwnd_ts < 60:
            return  # 同一窗口 60s 内不重复采集
        text = uia.capture_window_text(hwnd=hwnd, max_chars=2500)
        if not text or text == self._last_uia_text:
            return
        self._last_uia_text = text[:1500]
        source = "agent_chat" if self._is_agent_app(info.get("app", "")) else "terminal"
        self.pipeline.ingest({"source": source, "app": info.get("app"), "text": text[:1500], "time": datetime.now()})

    def _poll_docs(self) -> None:
        """PDF / Word / Excel 低频深采（watch_dirs 最近修改的文档）。"""
        from app.collector.docs import scan_recent_docs
        if not self.cfg.watch_dirs:
            return
        for doc in scan_recent_docs(self.cfg.watch_dirs):
            self.pipeline.ingest({"source": "document", "app": "docs", "title": doc["name"],
                                  "text": doc["text"][:1200], "time": doc["time"]})

    # ---------- 热信号即时批（v2.1） ----------

    def _hot_signal(self, text: str) -> None:
        """命中强标记 → 立即小批量提取（节流 30s），不等定时窗口。"""
        if self.llm is None or not self.llm.ready:
            return
        now = time.time()
        if now - self._last_hot < 30:
            return
        self._last_hot = now
        db = self.session_factory()
        try:
            rows = (
                db.query(MemoryShort)
                .filter(MemoryShort.processed == False,  # noqa: E712
                        MemoryShort.event_id.is_(None),
                        MemoryShort.source.in_(EXTRACT_SOURCES))
                .order_by(MemoryShort.id.desc())
                .limit(15)
                .all()
            )
            lines = "\n".join(f"- [{m.time:%H:%M}] {m.source}: {m.text[:150]}" for m in rows)
            ids = [m.id for m in rows]
        finally:
            db.close()
        if not lines.strip():
            return
        prompt = (
            "这是刚发生的一段工作过程（可能包含报错或关键进展）。立即压缩为事件。\n"
            "输出格式必须严格是 JSON 数组，每个元素是对象：\n"
            '[{"activity": "做了什么", "intent": "意图", "project": "项目名或null", "importance": 0-1}]\n'
            "只输出 JSON 数组。\n\n"
            f"{lines}"
        )
        try:
            raw = self.llm.chat(
                [{"role": "system", "content": "你是严谨的数据提取器，只输出合法 JSON。"},
                 {"role": "user", "content": prompt}],
                temperature=0.2,
            )
            events = self._parse_events(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("热信号提取失败: %s", exc)
            return
        db = self.session_factory()
        try:
            for ev in events:
                self.memory.remember(ev.get("activity", ""), source="collector_hot", app="collector",
                                     importance=float(ev.get("importance", 0.5)),
                                     project=ev.get("project"), activity=ev.get("activity"))
            for mid in ids:
                m = db.get(MemoryShort, mid)
                if m:
                    m.processed = True
            db.commit()
            log.info("热信号即时批：%d 个事件", len(events))
        finally:
            db.close()

    # ---------- 模型级批量提取 ----------

    def _batch_extract(self) -> None:
        if self.llm is None or not self.llm.ready:
            return
        db = self.session_factory()
        try:
            rows = (
                db.query(MemoryShort)
                .filter(MemoryShort.processed == False,  # noqa: E712
                        MemoryShort.event_id.is_(None),
                        MemoryShort.source.in_(EXTRACT_SOURCES))
                .order_by(MemoryShort.id)
                .limit(40)
                .all()
            )
            if not rows:
                return
            lines = "\n".join(f"- [{m.time:%H:%M}] {m.source}: {m.text[:120]}" for m in rows)
        finally:
            db.close()

        prompt = (
            "你是个人记忆整理助手。把下面的活动日志压缩为事件。\n"
            "输出格式必须严格是 JSON 数组，每个元素是对象：\n"
            '[{"activity": "做了什么", "intent": "意图/目的", '
            '"project": "项目名或null", "importance": 0-1, '
            '"need_screenshot": false}]\n'
            "need_screenshot 仅当纯文字完全无法理解场景、必须看图时填 true。"
            "只输出 JSON 数组，不要任何解释。\n\n"
            f"日志：\n{lines}"
        )
        try:
            raw = self.llm.chat(
                [{"role": "system", "content": "你是严谨的数据提取器，只输出合法 JSON。"},
                 {"role": "user", "content": prompt}],
                temperature=0.2,
            )
            events = self._parse_events(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("批量提取失败: %s", exc)
            return

        ids = [m.id for m in rows]
        db = self.session_factory()
        try:
            for ev in events:
                self.memory.remember(
                    ev.get("activity", ""),
                    source="collector_batch",
                    app="collector",
                    importance=float(ev.get("importance", 0.4)),
                    project=ev.get("project"),
                    activity=ev.get("activity"),
                )
            for mid in ids:
                m = db.get(MemoryShort, mid)
                if m:
                    m.processed = True
            db.commit()
            log.info("批量提取：%d 条日志 → %d 个事件", len(rows), len(events))
        finally:
            db.close()

        # AI 判断触发截图（need_screenshot）
        needs = [e for e in events if e.get("need_screenshot")]
        if needs:
            self._handle_screenshot_request(len(needs))

    # ---------- 条件截图 ----------

    def _handle_screenshot_request(self, count: int) -> None:
        """按截图策略处理 need_screenshot：auto→补拍；text_first→记录待确认提示；off→忽略。"""
        policy = self.cfg.screenshot_policy
        if policy == "off":
            return
        if policy == "auto":
            shot_dir = self.cfg.data_dir / "screenshots"
            path = None
            try:
                from app.collector import screenshot as shot_mod
                path = shot_mod.capture_window(shot_dir, label="ai")
            except Exception as exc:  # noqa: BLE001
                log.warning("截图失败: %s", exc)
            if path is not None:
                self.memory.remember(
                    f"截图待理解：{path.name}（视觉模型 Phase 3 接入后自动转为事件并删除图片）",
                    source="screenshot", importance=0.5, activity=f"截图待理解：{path.name}",
                )
                log.info("已补拍截图 %s（待视觉理解）", path.name)
        else:  # text_first：AI 触发只记录"待确认"提示
            self.memory.remember(
                f"AI 判断需要截图理解（文字不足）：{count} 处场景（text_first 策略未自动执行，可在设置开启 auto）",
                source="screenshot_hint", importance=0.4, activity="AI 判断需要截图理解（未执行）",
            )

    @staticmethod
    def _parse_events(raw: str) -> list[dict]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text
            text = text.strip().lstrip("json").strip()

        def _normalize(items) -> list[dict]:
            out = []
            for e in items:
                if isinstance(e, dict) and e.get("activity"):
                    out.append(e)
                elif isinstance(e, list):
                    # 兼容 ["activity", "xx", "project", "yy", ...] 数组对格式
                    d = {}
                    it = iter(e)
                    for k in it:
                        if isinstance(k, str) and k in ("activity", "intent", "project", "importance", "need_screenshot"):
                            try:
                                d[k] = next(it)
                            except StopIteration:
                                break
                    if d.get("activity"):
                        out.append(d)
            return out

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                data = data.get("events", data.get("data", []))
            return _normalize(data)
        except json.JSONDecodeError:
            start, end = text.find("["), text.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                    if isinstance(data, dict):
                        data = data.get("events", [])
                    return _normalize(data)
                except json.JSONDecodeError:
                    return []
            return []
