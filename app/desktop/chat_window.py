"""原生对话窗：pywebview（毛玻璃）→ msedge --app（WebView2 毛玻璃）→ 默认浏览器。

左键单击悬浮球只打开这个**独立对话窗**（/chat 页面：仅消息+输入框），
网页端在悬浮球右键菜单中打开。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import webbrowser

log = logging.getLogger("mindtrace.desktop.chat_window")

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
)


def _find_edge() -> str | None:
    for path in EDGE_CANDIDATES:
        if os.path.exists(path):
            return path
    found = shutil.which("msedge")
    return found


def open_chat_window(base_url: str, mode: str = "auto") -> bool:
    """打开独立对话窗（/chat 迷你页）。返回是否成功（失败则回退浏览器）。"""
    chat_url = base_url.rstrip("/") + "/chat"
    # 1) pywebview（若已安装）：无边框 + WebView2 毛玻璃
    if mode in ("auto", "pywebview"):
        try:
            import webview  # type: ignore

            webview.create_window("MindTrace", chat_url, width=460, height=700)
            webview.start()
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("pywebview 不可用: %s", exc)
    # 2) Edge 应用模式（WebView2 内核，backdrop-filter 毛玻璃 CSS 原生生效）
    if mode in ("auto", "edge"):
        edge = _find_edge()
        if edge:
            try:
                proc = subprocess.Popen(
                    [edge, "--app=" + chat_url, "--window-size=460,700"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if proc.poll() is None:
                    return True
            except Exception as exc:  # noqa: BLE001
                log.warning("Edge app 模式失败: %s", exc)
    # 3) 默认浏览器打开 /chat 页
    webbrowser.open(chat_url)
    return False
