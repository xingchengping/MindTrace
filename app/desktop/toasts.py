"""系统级 Toast（win11toast，Windows 10/11 通知中心样式）。

在受限环境（无 GUI / 库缺失）下优雅降级：返回 False，由通知中心（Web）兜底。
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("mindtrace.desktop.toasts")


def show_toast(title: str, body: str | None = None, buttons: list[dict] | None = None, on_activate: str | None = None) -> bool:
    """尝试弹出系统 Toast。buttons 如 [{"content": "查看", "arguments": "http://127.0.0.1:4000"}]。"""
    try:
        from win11toast import toast
    except Exception:  # pragma: no cover
        return False

    def _show():
        try:
            if buttons:
                toast(title, body, buttons=buttons)
            else:
                toast(title, body)
        except Exception as exc:  # pragma: no cover
            log.warning("Toast 显示失败: %s", exc)

    threading.Thread(target=_show, daemon=True).start()
    return True
