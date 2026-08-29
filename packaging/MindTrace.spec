# -*- mode: python ; coding: utf-8 -*-
# MindTrace PyInstaller 打包配置（单目录模式）
# 构建：pyinstaller packaging\MindTrace.spec --noconfirm

import sys
import os
from pathlib import Path

# SPECPATH 是 PyInstaller 注入的 spec 文件所在目录（如 I:\MindTrace\packaging）
PROJECT = Path(SPECPATH).resolve().parent  # packaging/ 的上一级 = 项目根
print(f"[spec] SPECPATH={SPECPATH} -> PROJECT={PROJECT}")

# tzdata 的时区数据是 tzdata/zoneinfo/ 下的一批无扩展名数据文件（TZif），zoneinfo
# 通过 importlib.resources 读取，而区域包（tzdata.zoneinfo.Asia 等）由 zoneinfo 动态
# import。两者都必须打包：子模块 + 数据文件。否则 APScheduler 用
# timezone="Asia/Shanghai" 会 ZoneInfoNotFoundError。
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

_TZDATA_SUBMODS = collect_submodules("tzdata")
_TZDATA_DATAS = collect_data_files("tzdata")
print(f"[spec] tzdata submodules: {len(_TZDATA_SUBMODS)}, data files: {len(_TZDATA_DATAS)}")

# APScheduler 3.x 靠 entry points（apscheduler-*.dist-info/entry_points.txt）发现
# 触发器/任务库/执行器插件；且触发器模块（interval/cron/date/combining…）全部要
# 打包。子模块整树 + 元数据都收，避免 "No trigger by the name" / "No module named
# apscheduler.triggers.date" 之类逐个爆。
_APSCHED_SUBMODS = collect_submodules("apscheduler")
_APSCHED_META = copy_metadata("APScheduler")
print(f"[spec] apscheduler submodules: {len(_APSCHED_SUBMODS)}, metadata: {len(_APSCHED_META)}")

# uvicorn 的 loop/http/ws/lifespan 全是 import_from_string("uvicorn.loops.auto"…) 动态
# 导入，静态分析抓不到 → 冻结后 uvicorn 线程直接 ModuleNotFoundError。整树收集。
_UVICORN_SUBMODS = collect_submodules("uvicorn")
print(f"[spec] uvicorn submodules: {len(_UVICORN_SUBMODS)}")

# anyio 按需动态导入后端（anyio._backends._asyncio/_trio），starlette/fastapi 全靠它，
# 漏了 API 请求一律 500。整树收集。
_ANYIO_SUBMODS = collect_submodules("anyio")
print(f"[spec] anyio submodules: {len(_ANYIO_SUBMODS)}")

# win11toast 原生 Toast 依赖 winrt（命名空间包 + _winrt*.pyd 扩展模块）。winrt 是
# 无 __init__.py 的命名空间包，必须整树收集，否则冻结后 import win11toast 失败，
# 攻克确认 Toast 静默降级。
_WINRT_SUBMODS = collect_submodules("winrt")
print(f"[spec] winrt submodules: {len(_WINRT_SUBMODS)}")

# PySide6 依赖 ICU DLL（wheel 不捆绑 icuuc/icuin），Qt6Core.dll 硬依赖 icuuc.dll；
# conda（D:\Anaconda\Library\bin）里有一个不兼容的 ICU 73，会被优先加载导致
# WinError 127。预载必须发生在 PyInstaller 的 Analysis / isolated（Qt 版本检测）
# 子进程里——spec 顶层代码只在主进程运行。把含 sitecustomize.py 的目录注入
# PYTHONPATH，所有构建子进程启动时自动按全路径预载 System32 版 ICU。
_ICU_BOOT = str(PROJECT / "packaging" / "icu_boot")
os.environ["PYTHONPATH"] = _ICU_BOOT + os.pathsep + os.environ.get("PYTHONPATH", "")
print(f"[spec] PYTHONPATH += {_ICU_BOOT}")

# Anaconda 基础环境依赖的 DLL：_ssl/_hashlib/_ctypes/_lzma/pyexpat/_tkinter/_sqlite3
# 等 pyd 依赖 libcrypto/ffi/sqlite3/liblzma/libexpat/tcl/tk 这些 DLL。它们不在
# <base>\DLLs（那里只有 pyd），而在 <base>\Library\bin（或某个 envs\*\Library\bin）。
# PyInstaller 的二进制搜索路径（pyd 目录 + base exe 目录 + 包导入新增路径）找不到它们，
# 会漏打包 → 冻结应用一 import 就崩。这里在候选目录里逐个解析，显式加进 binaries。
_BASE_DEPS = ["libcrypto-3-x64.dll", "libssl-3-x64.dll", "ffi.dll", "liblzma.dll",
              "libexpat.dll", "sqlite3.dll", "tcl86t.dll", "tk86t.dll"]
_CAND_DIRS = [os.path.join(sys.base_prefix, "DLLs"),
              os.path.join(sys.base_prefix, "Library", "bin"),
              os.path.join(sys.prefix, "Library", "bin")]
try:
    _CAND_DIRS += [d.path for d in os.scandir(os.path.join(sys.base_prefix, "envs"))
                   if d.is_dir() and os.path.isdir(os.path.join(d.path, "Library", "bin"))]
except OSError:
    pass
_EXTRA_BINARIES = []
for _dll in _BASE_DEPS:
    for _dir in _CAND_DIRS:
        _src = os.path.join(_dir, _dll)
        if os.path.exists(_src):
            _EXTRA_BINARIES.append((_src, "."))
            print(f"[spec] bundle dep {_dll} <- {_src}")
            break
    else:
        print(f"[spec] WARNING: dep not found: {_dll}")

# setuptools 内嵌的 jaraco.text 在模块导入时会读数据文件 Lorem ipsum.txt
# （pkg_resources 启动钩子 → jaraco.text → 读文件）。官方 hook 只收 .py 不收数据，
# 漏掉会直接崩溃（FileNotFoundError）。这里显式补上（落到 _internal\jaraco\text\）。
_EXTRA_DATAS = []
_SP = os.path.join(sys.prefix, "Lib", "site-packages")
_JARACO_TXT = os.path.join(_SP, "setuptools", "_vendor", "jaraco", "text", "Lorem ipsum.txt")
if os.path.exists(_JARACO_TXT):
    _EXTRA_DATAS.append((_JARACO_TXT, "jaraco/text"))
    print(f"[spec] bundle data {_JARACO_TXT}")
_EXTRA_DATAS += _TZDATA_DATAS  # tzdata 时区数据文件（无扩展名 TZif，importlib.resources 读取）
_EXTRA_DATAS += _APSCHED_META   # APScheduler entry points 元数据

# 语音功能运行时数据/二进制（全部懒导入，缺了不崩启动但功能静默失效 → 一并打包）
_PIPER_DATA = os.path.join(_SP, "piper", "espeak-ng-data")
if os.path.isdir(_PIPER_DATA):
    _EXTRA_DATAS.append((_PIPER_DATA, "piper/espeak-ng-data"))
    print(f"[spec] bundle data piper espeak-ng-data ({len(os.listdir(_PIPER_DATA))} 顶层条目)")
_SD_DATA = os.path.join(_SP, "_sounddevice_data")
if os.path.isdir(_SD_DATA):
    _EXTRA_DATAS.append((_SD_DATA, "_sounddevice_data"))
    print("[spec] bundle data _sounddevice_data (PortAudio)")
for _dll in ("libvosk.dll", "libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll"):
    _src = os.path.join(_SP, "vosk", _dll)
    if os.path.exists(_src):
        _EXTRA_BINARIES.append((_src, "vosk"))
        print(f"[spec] bundle dep {_dll} <- {_src}")

# certifi 的 cacert.pem 数据文件：import vosk → import requests → certifi 读证书，
# 漏打包会 FileNotFoundError [Errno 2]（语音转写静默失败；HTTPS 请求也会挂）。
_CERTIFI_PEM = os.path.join(_SP, "certifi", "cacert.pem")
if os.path.exists(_CERTIFI_PEM):
    _EXTRA_DATAS.append((_CERTIFI_PEM, "certifi"))
    print(f"[spec] bundle data certifi cacert.pem")

# 唤醒音效素材：mp3/wav 谁存在就打谁（用户可能只留一种，缺文件不能炸构建）
_MEDIA_DATAS = []
for _m in ("music.mp3", "music.wav"):
    _mp = PROJECT / _m
    if _mp.exists():
        _MEDIA_DATAS.append((str(_mp), "."))
        print(f"[spec] bundle media {_m}")

a = Analysis(
    [str(PROJECT / "main.py")],
    pathex=[str(PROJECT)],
    binaries=_EXTRA_BINARIES,
    datas=[
        (str(PROJECT / "app" / "web"), "app/web"),                      # 前端
        (str(PROJECT / "app" / "desktop" / "bubble.html"), "app/desktop"),  # 果冻球页面
        (str(PROJECT / "config.yaml"), "."),                            # 默认配置
        (str(PROJECT / "llama-b8981-bin-win-cpu-x64"), "llama-b8981-bin-win-cpu-x64"),  # llama.cpp
        # Piper1（语音合成）：Python 包内 espeak-ng-data + espeakbridge.pyd + 中文模型
        (str(PROJECT / "models" / "zh_CN-huayan-medium.onnx"), "models"),
        (str(PROJECT / "models" / "zh_CN-huayan-medium.onnx.json"), "models"),
    ] + _MEDIA_DATAS + _EXTRA_DATAS,
    hiddenimports=[
        # pywin32（采集 + SAPI 语音 + 托盘）
        "win32gui", "win32process", "win32api", "win32clipboard", "win32com",
        "win32com.client", "win32timezone", "pywintypes", "win32event",
        # UIAutomation
        "comtypes", "comtypes.gen", "comtypes.stream",
        # SQLAlchemy（含 greenlet 异步依赖）
        "sqlalchemy.dialects.sqlite", "sqlalchemy.orm", "greenlet",
        # 托盘/通知
        "pystray._win32", "PIL._tkinter_finder", "win11toast",
        # 调度（APScheduler 整树：triggers/executors/jobstores 经 entry points 动态加载）
        "apscheduler.schedulers.background", "tzlocal", *_APSCHED_SUBMODS,
        # tzdata 整树（zoneinfo 动态导入时区模块，如 tzdata.zoneinfo.Asia.Shanghai）
        *_TZDATA_SUBMODS,
        # winrt 整树（win11toast 原生 Toast 依赖；命名空间包需显式收集）
        *_WINRT_SUBMODS,
        # uvicorn 整树（loop/http/ws/lifespan 动态导入）
        *_UVICORN_SUBMODS,
        # anyio 整树（后端动态导入，starlette/fastapi 依赖）
        *_ANYIO_SUBMODS,
        # 数据科学
        "networkx.algorithms.traversal", "numpy",
        # 文档/表格/Git 采集
        "openpyxl", "docx", "pypdf",
        # 网络（模型下载/HTTP）
        "requests", "urllib3", "certifi",
        # 语音（录音 + Vosk 转写 + Piper1 合成）
        "sounddevice", "vosk", "cffi", "piper", "onnxruntime",
        "piper.phonemize_espeak", "piper.phoneme_ids",
        # Qt 果冻球（Chromium）
        "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
        "PySide6.QtNetwork", "PySide6.QtWebChannel", "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    ],
    runtime_hooks=[str(PROJECT / "packaging" / "rthooks" / "stdio.py")],
    excludes=["PyQt5", "PyQt6", "PySide2", "matplotlib", "scipy", "pandas",
              "transformers", "dask", "datasets", "rich", "onnx", "keras",
              "pytest", "mypy", "notebook", "jupyter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MindTrace",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # 无控制台窗口（悬浮球 + 网页）
    icon=str(PROJECT / "logo.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MindTrace",
)
