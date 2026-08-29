"""UI Automation 文本采集（comtypes + UIAutomation）。

用于：终端日志、Agent 对话正文、无标题窗口的界面文本。
Windows Terminal / VS Code 终端等现代应用均暴露 UIA 文本。
受限环境（无 comtypes / UIA 失败）下优雅降级。
"""
from __future__ import annotations

import logging

log = logging.getLogger("mindtrace.collector.uia")

_available = None

try:
    import comtypes.client  # noqa: WPS433
    from comtypes import CoCreateInstance, CLSCTX_ALL, GUID  # noqa: WPS433

    _UIAutomation = GUID("{ff48dba4-60ef-4201-aa87-54103eef594e}")
    _IUIAutomation = GUID("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")
    _available = True
except Exception:  # pragma: no cover
    _available = False


def available() -> bool:
    return bool(_available)


def capture_window_text(hwnd: int | None = None, max_chars: int = 3000) -> str:
    """通过 UIAutomation 读取窗口的可见文本（正文）。"""
    if not _available:
        return ""
    try:
        automation = CoCreateInstance(_UIAutomation, interface=_IUIAutomation, clsctx=CLSCTX_ALL)
        if hwnd:
            root = automation.ElementFromHandle(hwnd)
        else:
            root = automation.GetFocusedElement()
        if root is None:
            return ""
        parts: list[str] = []

        def walk(element, depth: int = 0):
            if depth > 12 or len("\n".join(parts)) > max_chars:
                return
            try:
                if element.CurrentControlType in (50033,):  # 文本控件
                    name = element.CurrentName
                    if name and name not in parts:
                        parts.append(name)
            except Exception:
                pass
            try:
                children = element.FindAll(
                    0, automation.CreateTrueCondition()
                )  # TreeScope_Descendants
                for i in range(children.Length):
                    walk(children.GetElement(i), depth + 1)
            except Exception:
                pass

        try:
            walk(root)
        except Exception:  # noqa: BLE001
            pass
        text = "\n".join(parts).strip()
        return text[:max_chars]
    except Exception as exc:  # pragma: no cover
        log.debug("UIA 采集失败: %s", exc)
        return ""
