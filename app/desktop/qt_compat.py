"""Qt 兼容层：必须在任何 PySide6 导入之前调用。

1. 受限环境下 Chromium 子进程沙箱可能失败 → 关沙箱（QTWEBENGINE_DISABLE_SANDBOX）
2. Qt Quick 软件渲染兜底（GPU 上下文失败也能合成上屏）
3. Chromium 单进程 + 便携数据目录（缓存写进 exe 旁 data/，不污染 %LOCALAPPDATA%）
4. 预载 Windows 自带 ICU：PySide6 wheel 不捆绑 icuuc.dll，而 Qt6Core.dll 硬依赖它；
   按全路径预载 System32 版，修复 WinError 127（找不到指定的程序）。
"""
from __future__ import annotations

import ctypes
import logging
import os

log = logging.getLogger("mindtrace.desktop.qt_compat")

_ICU_DLLS = ("icuuc.dll", "icuin.dll")


def _setup_env() -> None:
    """Chromium / Qt 环境默认值（幂等；用户可用环境变量覆盖）。

    - 标准多进程 + GPU 渲染：快速滚动不重叠、丝滑（单进程模式合成时序有缺陷，滚动会文字重叠）
    - 关沙箱（QTWEBENGINE_DISABLE_SANDBOX + --no-sandbox）：兼容受限环境/杀软
    - 如需回退单进程/软件渲染（驱动异常、受管机器等），自行设置环境变量：
      QTWEBENGINE_CHROMIUM_FLAGS 追加 --single-process --disable-gpu；QT_QUICK_BACKEND=software
    """
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    if "QTWEBENGINE_CHROMIUM_FLAGS" not in os.environ:
        from app.core.paths import EXE_DIR  # 延迟导入，避免循环

        data_dir = EXE_DIR / "data" / "qtwebengine"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # pragma: no cover
            data_dir = None
        flags = "--no-sandbox"
        if data_dir is not None:
            flags += " --user-data-dir=" + data_dir.as_posix()
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = flags


def _preload_icu() -> None:
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    for name in _ICU_DLLS:
        p = os.path.join(sysroot, "System32", name)
        if not os.path.exists(p):
            continue
        try:
            ctypes.WinDLL(p)
            log.debug("预载 %s", p)
        except OSError as exc:  # noqa: BLE001
            log.warning("预载 %s 失败: %s", p, exc)


def preload_qt() -> None:
    """Qt 导入前必须调用（幂等，失败静默——后续 import 失败会走 Tk 回退）。"""
    _setup_env()
    _preload_icu()


preload_qt()  # 模块导入即预载
