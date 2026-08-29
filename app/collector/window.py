"""前台窗口采集（Win32 API，轻量高频）。

在无 pywin32 的环境（如受限沙箱）下优雅降级：返回 None 并只警告一次。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("mindtrace.collector.window")

_warned = False

try:
    import win32gui
    import win32process
    import win32api
    import psutil

    _HAS_WIN32 = True
except Exception:  # pragma: no cover
    _HAS_WIN32 = False

_TITLE_SPLIT_RE = re.compile(r"\s*[-–—|·]\s*")


def available() -> bool:
    return _HAS_WIN32


def list_open_windows(limit: int = 60) -> list[dict]:
    """枚举当前打开的顶层窗口（供"锁定单窗口"选择）。"""
    if not _HAS_WIN32:
        return []
    found: list[dict] = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title or len(title) < 2:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            app = ""
            try:
                app = psutil.Process(pid).name()
            except Exception:
                pass
        except Exception:
            app = ""
        found.append({"hwnd": hwnd, "title": title[:80], "app": app or "unknown"})

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception as exc:  # pragma: no cover
        log.warning("窗口枚举失败: %s", exc)
    # 去重（同标题+同应用只保留一个）
    seen: set[tuple] = set()
    deduped = []
    for w in found:
        key = (w["title"], w["app"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(w)
    return deduped[:limit]


def get_foreground_window() -> dict | None:
    """返回 {app, title, pid}；失败返回 None。"""
    global _warned
    if not _HAS_WIN32:
        if not _warned:
            log.warning("pywin32 不可用，窗口采集已停用")
            _warned = True
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd).strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        app = ""
        try:
            app = psutil.Process(pid).name()
        except Exception:
            pass
        if not title and not app:
            return None
        return {"app": app or "unknown", "title": title, "pid": pid}
    except Exception as exc:  # pragma: no cover
        if not _warned:
            log.warning("窗口采集失败: %s", exc)
            _warned = True
        return None


def parse_title(app: str, title: str) -> dict:
    """规则级解析标题：项目 / 文件 / 活动。

    "model.py — MyProject - Visual Studio Code" → {project: MyProject, file: model.py}
    """
    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(title) if p.strip()]
    result: dict = {"app": app, "title": title, "activity": title}
    if not parts:
        return result
    app_lower = (app or "").lower()
    # 常见 IDE/编辑器：最后一节是应用名，前一节可能是项目，其余是文件
    if any(k in app_lower for k in ("code", "idea", "pycharm", "sublime", "notepad", "atom", "vim", "cursor")):
        file = parts[0]
        if "." in file:  # 像是文件名
            result["file"] = file
        if len(parts) >= 3:
            result["project"] = parts[-2]
        elif len(parts) == 2:
            result["project"] = parts[-1]
    # 浏览器
    if any(k in app_lower for k in ("chrome", "edge", "firefox", "msedge", "browser")):
        if parts:
            result["activity"] = f"浏览：{parts[0]}"
    return result
