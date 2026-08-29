"""条件截图（mss）：仅在规则触发 / AI need_screenshot 时使用，理解后删除。

Phase 2 实现：捕获 → 暂存 data/screenshots/ + 生成"待理解"事件/通知 →
视觉模型（Phase 3）理解成 Event 后立即删除图片。
策略：text_first（默认，仅规则触发）/ auto（允许 AI 判断触发）/ off（关闭）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger("mindtrace.collector.screenshot")


def capture_window(screenshot_dir: Path, hwnd: int | None = None, label: str = "window") -> Path | None:
    """截取指定窗口（或全屏）。返回图片路径。"""
    try:
        import mss
        import mss.tools
    except Exception:  # pragma: no cover
        log.warning("mss 不可用，截图已停用")
        return None
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{label}.png"
    path = screenshot_dir / name
    try:
        with mss.mss() as sct:
            if hwnd:
                try:
                    import win32gui
                    rect = win32gui.GetWindowRect(hwnd)
                    monitor = {
                        "left": rect[0], "top": rect[1],
                        "width": max(rect[2] - rect[0], 1), "height": max(rect[3] - rect[1], 1),
                    }
                    shot = sct.grab(monitor)
                except Exception:
                    shot = sct.grab(sct.monitors[0])
            else:
                shot = sct.grab(sct.monitors[0])
            mss.tools.to_png(shot.rgb, shot.size, output=str(path))
        return path
    except Exception as exc:  # pragma: no cover
        log.warning("截图失败: %s", exc)
        return None
