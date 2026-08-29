"""轻量采集源：剪贴板 / Git 日志 / 浏览器历史（均为授权项，默认关）。"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger("mindtrace.collector.sources")

# GUI 版子进程不弹控制台窗口（git 是控制台程序，采集触发时避免闪黑窗）
_WIN_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

_warned_clip = False


# ---------- 剪贴板 ----------

def clip_text() -> str | None:
    """读取剪贴板纯文本；失败返回 None（不抛异常）。"""
    global _warned_clip
    try:
        import win32clipboard  # noqa: WPS433

        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception as exc:  # pragma: no cover
        if not _warned_clip:
            log.warning("剪贴板采集不可用: %s", exc)
            _warned_clip = True
    return None


# ---------- Git ----------

def git_recent(dir_path: str, limit: int = 5) -> list[dict]:
    """读取指定目录的最近 git 提交（--since=今天）。"""
    try:
        proc = subprocess.run(
            ["git", "-C", dir_path, "log", "--since=24 hours ago",
             f"--max-count={limit}", "--pretty=format:%H|%an|%s"],
            capture_output=True, text=True, timeout=15, **_WIN_NO_WINDOW,
        )
    except Exception:
        return []
    rows = []
    for line in proc.stdout.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            rows.append({"sha": parts[0][:8], "author": parts[1], "subject": parts[2]})
    return rows


# ---------- 浏览器历史 ----------

def _history_db_path(browser: str) -> Path | None:
    base = Path.home() / "AppData" / "Local"
    if browser == "chrome":
        return base / "Google" / "Chrome" / "User Data" / "Default" / "History"
    if browser == "edge":
        return base / "Microsoft" / "Edge" / "User Data" / "Default" / "History"
    return None


def browser_recent(browser: str = "edge", limit: int = 20) -> list[dict]:
    """读取浏览器最近浏览记录（影子拷贝，避免文件锁）。"""
    src = _history_db_path(browser)
    if src is None or not src.exists():
        return []
    dest = Path.home() / "AppData" / "Local" / "Temp" / f"mt_history_{browser}.db"
    try:
        shutil.copyfile(src, dest)  # 浏览器可能占用，复制后读取
        conn = sqlite3.connect(dest)
        try:
            cur = conn.execute(
                "SELECT url, title, visit_time FROM urls "
                "WHERE visit_time > ? ORDER BY visit_time DESC LIMIT ?",
                ((datetime.now().timestamp() - 86400) * 1_000_000, limit),
            )
            rows = [
                {"url": r[0], "title": r[1] or r[0], "time": datetime.fromtimestamp(r[2] / 1_000_000)}
                for r in cur.fetchall()
            ]
        finally:
            conn.close()
        return rows
    except Exception as exc:  # pragma: no cover
        log.debug("浏览器历史读取失败: %s", exc)
        return []
