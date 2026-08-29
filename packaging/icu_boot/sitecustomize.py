"""构建期 ICU 预载（PyInstaller 子进程自动导入）。

背景：PySide6 wheel 不捆绑 icuuc.dll/icuin.dll，而 Qt6Core.dll 硬依赖它们。
本机 conda（D:\\Anaconda\\Library\\bin）有一个不兼容的 ICU 73，会被加载器优先
找到，导致 "DLL load failed while importing QtCore: 找不到指定的程序"
(WinError 127)。按全路径预载 System32 版 ICU 后，加载器会复用已加载模块，
不再搜索坏版本。

生效方式：spec 顶层把本目录注入 PYTHONPATH。PyInstaller 的 Analysis 子进程与
isolated（Qt 版本检测）子进程都继承该环境变量并在启动时自动导入 sitecustomize，
因此在任何 PySide6 导入发生前完成预载。
"""
import ctypes
import os

for _icu in ("icuuc.dll", "icuin.dll"):
    _p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", _icu)
    if os.path.exists(_p):
        try:
            ctypes.WinDLL(_p)
        except OSError:
            pass
