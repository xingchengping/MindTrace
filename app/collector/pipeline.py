"""事件提取管线：原始捕获 → 短期日志 → 规则级即时事件 + 模型级批量压缩。"""
from __future__ import annotations

import logging
from datetime import datetime

from app.core.models import Event, MemoryShort

log = logging.getLogger("mindtrace.collector.pipeline")

# 规则级关键字（问题/成功信号）
PROBLEM_KW = ("报错", "错误", "异常", "失败", "error", "failed", "exception", "bug", "卡住", "不行", "崩溃", "cannot", "can't")
SUCCESS_KW = ("解决", "搞定", "完成", "通过", "成功", "fixed", "works", "success", "passed", "done")
FOCUS_KW = ("正在", "研究", "实现", "重构", "优化", "调试", "修复", "开发", "测试", "尝试")


def _importance_for(source: str, text: str, kind: str | None = None) -> float:
    low = text.lower()
    if any(k in low for k in SUCCESS_KW):
        return 0.6
    if any(k in low for k in PROBLEM_KW):
        return 0.55
    if source == "file" and kind in ("created", "modified"):
        return 0.4
    if source == "git":
        return 0.5
    if source == "agent_chat":
        return 0.45
    if source == "window":
        return 0.25
    return 0.3


def _project_hint(row: dict) -> str | None:
    for key in ("project", "dir"):
        v = row.get(key)
        if v:
            return str(v).replace("\\", "/").rstrip("/").split("/")[-1]
    return None


def _activity_text(row: dict) -> str:
    source = row.get("source", "")
    if source == "window":
        return row.get("activity") or row.get("title") or ""
    if source == "file":
        kind = row.get("kind", "modified")
        return f"{kind}文件：{row.get('path', '')}"
    if source == "clipboard":
        return f"剪贴板：{row.get('text', '')[:200]}"
    if source == "agent_chat":
        return f"Agent对话：{row.get('text', '')[:200]}"
    if source == "git":
        return f"提交：{row.get('subject', '')}"
    if source == "browser":
        return f"浏览：{row.get('title', row.get('url', ''))}"
    return str(row.get("text", ""))[:200]


class CapturePipeline:
    """接收采集器捕获，写短期日志；规则级即时提炼显著事件；热信号即时批。"""

    def __init__(self, session_factory, memory, llm=None, hot_keywords: tuple[str, ...] = (),
                 on_hot=None):
        self.session_factory = session_factory
        self.memory = memory
        self.llm = llm
        self.hot_keywords = hot_keywords
        self.on_hot = on_hot  # 回调（热信号 → 任务引擎/即时提取）

    def ingest(self, row: dict) -> Event | None:
        """写入 memories_short；规则级命中则立即创建 Event；热信号触发即时批。"""
        text = _activity_text(row)
        importance = _importance_for(row.get("source", ""), text, row.get("kind"))
        # 热信号即时批（v2.1）：强标记 → 不等定时窗口
        if self.hot_keywords and any(k.lower() in text.lower() for k in self.hot_keywords):
            if self.on_hot is not None:
                try:
                    self.on_hot(text)
                except Exception:  # noqa: BLE001
                    pass
        db = self.session_factory()
        try:
            mem = MemoryShort(
                time=row.get("time") or datetime.now(),
                source=row.get("source", "unknown"),
                app=row.get("app"),
                text=text,
                importance=importance,
                processed=False,
            )
            db.add(mem)
            db.commit()
            db.refresh(mem)
            mem_id = mem.id
        finally:
            db.close()

        # 规则级即时事件：问题/成功信号 或 文件/Git 显著活动
        notable = importance >= 0.4 or row.get("source") in ("git", "file", "agent_chat")
        if not notable:
            return None
        ev = self.memory.remember(
            text,
            source=row.get("source", "collector"),
            app=row.get("app") or "collector",
            importance=importance,
            project=_project_hint(row),
            activity=text,
        )
        if ev is not None:
            db = self.session_factory()
            try:
                m = db.get(MemoryShort, mem_id)
                if m:
                    m.processed = True
                    m.event_id = ev.id
                    db.commit()
            finally:
                db.close()
        return ev
