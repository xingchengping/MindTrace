# 打包为 exe（无 Python 用户可用）

目标：`MindTrace.exe` 双击即用——自带 Python 运行时、llama.cpp 二进制、Web 前端；
模型由用户首次运行时下载（模型体积大，不打进包）。

## 一键构建

```powershell
# 1. 安装打包工具（在项目 venv 内）
.\.venv\Scripts\pip install pyinstaller

# 2. 构建（单目录模式，含全部依赖）
.\.venv\Scripts\pyinstaller packaging\MindTrace.spec --noconfirm

# 3. 产物在 dist\MindTrace\
#    拷贝到任意目录即可运行；首次运行自动生成 config.yaml 与 data/、models/
```

> 用 **单目录（onedir）** 而非单文件（onefile）：llama-server.exe 是子进程，
> 子进程不能从 onefile 的临时解包目录可靠启动，且 onefile 启动慢、易被杀软误报。

## 打包内容（MindTrace.spec 要点）

| 内容 | 处理 |
|---|---|
| 全部 Python 依赖 | PyInstaller 自动收集；**hiddenimports** 见下 |
| `app/web/*`（前端） | `--add-data "app/web;app/web"`（运行时在 `_MEIPASS/app/web`，代码已用 `RESOURCE_DIR` 解析） |
| `app/desktop/bubble.html`（果冻球页面） | `--add-data` 捆绑（运行时 `_MEIPASS/app/desktop/bubble.html`） |
| `config.yaml` | 同上捆绑；首次运行自动复制到 exe 旁（`paths.resolve_config_path`） |
| `llama-b8981-bin-win-cpu-x64/*` | `--add-data` 捆绑；运行时从 exe 旁或 `_MEIPASS` 启动（`EXE_DIR/llama-.../llama-server.exe`） |
| GGUF 模型 | **不打进包**（2-5GB）；首次运行 `MindTrace.exe --download-models` 下载到 exe 旁 `models/` |
| PySide6 + QtWebEngine（果冻球） | PyInstaller 自带 hook；`hiddenimports` 见下；Qt 库约 +400MB |
| 图标 | `icon.ico`（`icon='packaging/icon.ico'`） |

## hiddenimports（PyInstaller 检测不到的关键依赖）

```python
hiddenimports = [
    # pywin32（采集）
    "win32gui", "win32process", "win32api", "win32clipboard",
    # UIAutomation（comtypes 需生成 gen）
    "comtypes", "comtypes.gen",
    # SQLAlchemy dialects（sqlite）
    "sqlalchemy.dialects.sqlite",
    # 托盘 / 通知
    "pystray._win32", "PIL._tkinter_finder",
    # 调度
    "apscheduler.triggers.interval", "apscheduler.triggers.cron",
    "apscheduler.schedulers.background", "tzlocal",
    # 数据科学
    "networkx.algorithms.traversal", "numpy",
    # Qt 果冻球（Chromium 渲染悬浮球）
    "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
    "PySide6.QtNetwork", "PySide6.QtWebChannel", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
]
```

## 冻结模式下的路径约定（代码已就绪，见 `app/core/paths.py`）

```
MindTrace.exe（exe 旁 = EXE_DIR，可写、可整体拷贝）
├── config.yaml          # 首次运行自动生成（可改：档位/开关/端口）
├── data/                # SQLite、日志、截图
├── models/              # GGUF（--download-models 下载）
└── llama-.../           # llama.cpp 二进制（打包时捆绑）
```

- `EXE_DIR` = exe 所在目录；`RESOURCE_DIR` = 打包资源（web/config，只读）
- 开发模式两者都 = 项目根，行为不变

## 验证清单（打包后）

1. `MindTrace.exe --download-models` → 模型下载到 exe 旁 models/
2. `MindTrace.exe` → 悬浮球出现、http://127.0.0.1:4000 可打开、能对话
3. 关悬浮球 → 干净退出（无残留 llama-server 进程）
4. 拷贝整个目录到另一台无 Python 的机器 → 同样可用
5. 杀软可能误报（子进程+本机端口）→ 建议代码签名

## 常见坑

- **onefile 不行**：子进程 + 大体积 + 杀软
- **pywin32 必须 hiddenimports**，否则采集静默失败
- **comtypes.gen** 首次会尝试写 gen 目录 → 打包时预生成或设 `comtypes.client.gen_dir` 到可写位置
- **模型不打包**：包体 ~150-250MB（llama 二进制 + 运行时）；启用 Qt 果冻球（PySide6 + QtWebEngine）后约 +400MB，总 ~600-700MB（Chromium 引擎体积，换取浏览器级果冻球动画）
- **QtWebEngine 打包**：PyInstaller 的 PySide6 hook 会自动收集 QtWebEngineProcess.exe 与资源；若打包后悬浮球白屏，检查 `dist/MindTrace/_internal` 下是否含 `PySide6/Qt/libexec/QtWebEngineProcess.exe` 及 `resources/`、`translations/`（必要时 `--collect-all PySide6`）
- **QtWebEngine 子进程沙箱**：代码已设 `QTWEBENGINE_DISABLE_SANDBOX=1`，避免受限环境（无权限/杀软）下 Chromium 沙箱失败
- **Qt 默认参数**：标准**多进程 + GPU 渲染**（快速滚动不重叠、丝滑；单进程模式合成时序有缺陷会文字重叠）+ `--no-sandbox` + 便携 `--user-data-dir=exe旁/data/qtwebengine`（不污染 %LOCALAPPDATA%）；受限环境可设 `QTWEBENGINE_CHROMIUM_FLAGS` 追加 `--single-process --disable-gpu`、或设 `QT_QUICK_BACKEND=software` 回退
- **ICU 兼容层**（`app/desktop/qt_compat.py`）：部分 Windows 上 PySide6 的 Qt6Core.dll 依赖的 icuuc.dll 不在 wheel 里，按全路径预载 System32 版修复 `WinError 127`；导入 PySide6 前自动执行
