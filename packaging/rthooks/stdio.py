# -*- mode: python ; coding: utf-8 -*-
"""窗口模式（console=False）运行时钩子。

PyInstaller 窗口模式把 sys.stdout / sys.stderr 置为 None，而 uvicorn 的
DefaultFormatter 构造时调 sys.stdout.isatty()（uvicorn/logging.py），
None.isatty() 直接 AttributeError 崩溃（"Unable to configure formatter 'default'"）。
这里把 None 重定向到 exe 旁 data/logs/console.log（顺带保留 print 输出便于排障）。
"""
import os
import sys


def _open_log() -> object:
    try:
        base = os.path.dirname(os.path.abspath(sys.executable))
        log_dir = os.path.join(base, "data", "logs")
        os.makedirs(log_dir, exist_ok=True)
        return open(os.path.join(log_dir, "console.log"), "a", encoding="utf-8", buffering=1)
    except Exception:  # noqa: BLE001  # 任何异常都退回 devnull，绝不让钩子本身成为崩溃点
        return open(os.devnull, "w")


if sys.stdout is None:
    sys.stdout = _open_log()
if sys.stderr is None:
    sys.stderr = _open_log()
