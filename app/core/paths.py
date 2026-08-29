"""路径解析：同时支持开发模式与 PyInstaller 冻结（exe）模式。

- EXE_DIR：可执行文件所在目录 —— 数据/模型/日志/llama 二进制都放这里
  （用户可见、可写、可整体拷贝便携；开发时 = 项目根）
- RESOURCE_DIR：捆绑只读资源目录 —— web 静态文件、默认 config
  （冻结时 = PyInstaller 的 _MEIPASS 临时解包目录）
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_dir() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", exe_dir()))
    return Path(__file__).resolve().parents[2]


EXE_DIR = exe_dir()
RESOURCE_DIR = resource_dir()
