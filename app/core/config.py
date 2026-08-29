"""配置加载与硬件档位探测。"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import yaml

from app.core.paths import EXE_DIR, RESOURCE_DIR, is_frozen

# 默认配置（捆绑只读）与用户配置（exe 旁可写，首次运行复制）
BUNDLED_CONFIG = RESOURCE_DIR / "config.yaml"
USER_CONFIG = EXE_DIR / "config.yaml"

PROFILES = ("light", "standard", "powerful")
DEFAULT_SERVER_REL = Path("llama-b8981-bin-win-cpu-x64") / "llama-server.exe"


def _resolve_server_path(rel: str) -> Path:
    """llama-server 位置：优先 exe 旁（便携布局，可整体拷贝/替换），
    冻结时若 exe 旁不存在则回退到 PyInstaller 捆绑目录（_internal）。"""
    p = EXE_DIR / rel
    if not p.exists() and is_frozen():
        p = RESOURCE_DIR / rel
    return p


def resolve_config_path() -> Path:
    """返回实际使用的配置文件：
    开发模式 = 项目根 config.yaml；冻结模式 = exe 旁 config.yaml（不存在则从捆绑复制）。"""
    if is_frozen() and not USER_CONFIG.exists() and BUNDLED_CONFIG.exists():
        try:
            shutil.copyfile(BUNDLED_CONFIG, USER_CONFIG)
            print(f"[config] 已生成用户配置：{USER_CONFIG}")
        except Exception as exc:  # noqa: BLE001
            print(f"[config] 复制配置失败: {exc}")
    if USER_CONFIG.exists():
        return USER_CONFIG
    return BUNDLED_CONFIG


def detect_ram_gb() -> float:
    try:
        return psutil.virtual_memory().total / (1024**3)
    except Exception:
        return 8.0


def detect_physical_cores() -> int:
    try:
        cores = psutil.cpu_count(logical=False)
        if cores and cores > 0:
            return cores
    except Exception:
        pass
    try:
        cores = psutil.cpu_count(logical=True)
        if cores and cores > 0:
            return cores
    except Exception:
        pass
    return 4


def detect_profile() -> str:
    """模型分两套，以 8GB 为界：≤8GB 用轻量模型（给工作+系统留足内存）。"""
    ram_gb = detect_ram_gb()
    if ram_gb <= 8:
        return "light"
    if ram_gb <= 16:
        return "standard"
    return "powerful"


@dataclass
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 4000
    profile: str = "standard"
    profile_detected: str = "standard"
    data_dir: Path = EXE_DIR / "data"
    models_dir: Path = EXE_DIR / "models"
    log_level: str = "INFO"
    model_path: Path | None = None
    n_ctx: int = 4096
    n_threads: int = 4
    n_batch: int = 256
    n_parallel: int = 1
    server_path: Path = EXE_DIR / DEFAULT_SERVER_REL
    server_host: str = "127.0.0.1"
    server_port: int = 8081
    server_timeout: int = 180
    emb_server_path: Path = EXE_DIR / DEFAULT_SERVER_REL
    emb_server_host: str = "127.0.0.1"
    emb_server_port: int = 18082
    emb_server_timeout: int = 120
    emb_model_path: Path | None = None
    emb_ctx: int = 512
    emb_batch: int = 128
    emb_threads: int = 4
    vis_server_path: Path = EXE_DIR / DEFAULT_SERVER_REL
    vis_server_host: str = "127.0.0.1"
    vis_server_port: int = 18083
    vis_server_timeout: int = 120
    vis_model_path: Path | None = None
    vis_mmproj_path: Path | None = None
    vis_ctx: int = 4096
    vis_batch: int = 256
    vis_threads: int = 6
    vis_lazy: bool = False       # light 档：视觉按需懒启动（有截图待理解才启动）
    memory_top_k: int = 5
    memory_min_score: float = 0.35
    memory_auto_remember: bool = True
    memory_dedup_threshold: float = 0.95
    # 采集
    collector_enabled: bool = True
    monitor_mode: str = "follow"          # follow | locked | off
    locked_pattern: str = ""
    monitor_interval: int = 3
    start_paused: bool = True             # 启动即暂停采集（菜单显示"开始采集"；点击开始才采集）
    watch_dirs: list[str] = field(default_factory=list)
    clipboard_enabled: bool = False
    browser_history: bool = False
    git_enabled: bool = False
    batch_minutes: int = 5
    screenshot_policy: str = "text_first"  # text_first | auto | off
    agent_apps: list[str] = field(default_factory=list)
    # 任务引擎
    blocked_persist_batches: int = 2
    solved_min_signals: int = 2
    focus_bullet_cap: int = 200
    compaction_batch: int = 50
    hot_keywords: list[str] = field(default_factory=list)
    recall_minutes: int = 15
    recall_threshold: float = 0.85
    # 桌面层
    desktop_enabled: bool = True
    bubble_enabled: bool = True
    tray_enabled: bool = True
    toasts_enabled: bool = True
    chat_window_mode: str = "auto"        # auto | edge | browser
    # 巩固
    short_retention_hours: int = 24
    event_retention_days: int = 180
    trash_days: int = 30
    caps: dict = field(default_factory=dict)
    consolidation_daily: str = "02:30"
    raw: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str | None = None,
             profile: str | None = None) -> "AppConfig":
        """加载配置。path 缺省时自动解析（exe 旁用户配置 / 捆绑默认配置）；
        profile 参数可强制档位（CLI --profile），否则按 config/内存探测。"""
        cfg_path = Path(path) if path is not None else resolve_config_path()
        raw: dict = {}
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        else:
            print(f"[config] 未找到 {cfg_path}，使用默认配置")

        app_cfg = raw.get("app", {}) or {}
        profiles_cfg = raw.get("profiles", {}) or {}
        llm_cfg = raw.get("llm", {}) or {}

        cfg = cls()
        cfg.raw = raw
        cfg.host = str(app_cfg.get("host", cfg.host))
        cfg.port = int(app_cfg.get("port", cfg.port))
        cfg.data_dir = EXE_DIR / str(app_cfg.get("data_dir", "data"))
        cfg.models_dir = EXE_DIR / str(app_cfg.get("models_dir", "models"))
        cfg.log_level = str(app_cfg.get("log_level", cfg.log_level)).upper()

        requested = str(profile or app_cfg.get("profile", "auto")).lower()
        detected = detect_profile()
        cfg.profile_detected = detected
        cfg.profile = detected if requested == "auto" else requested
        if cfg.profile not in PROFILES:
            print(f"[config] 未知档位 {cfg.profile}，回退 standard")
            cfg.profile = "standard"

        p = profiles_cfg.get(cfg.profile, {}) or {}
        cfg.n_ctx = int(p.get("ctx", cfg.n_ctx))
        cfg.n_batch = int(p.get("batch", cfg.n_batch))
        cfg.n_parallel = int(p.get("parallel", 1))
        threads = p.get("threads", "auto")
        cfg.n_threads = detect_physical_cores() if threads in (None, "auto") else int(threads)

        model_name = str(p.get("model", ""))
        cfg.model_path = cfg.models_dir / model_name if model_name else None

        # llama-server 配置
        server_cfg = llm_cfg.get("server", {}) or {}
        cfg.server_path = _resolve_server_path(str(server_cfg.get("path", DEFAULT_SERVER_REL)))
        cfg.server_host = str(server_cfg.get("host", cfg.server_host))
        cfg.server_port = int(server_cfg.get("port", cfg.server_port))
        cfg.server_timeout = int(server_cfg.get("start_timeout", cfg.server_timeout))

        # embedding llama-server 配置
        emb_cfg = llm_cfg.get("embedding", {}) or {}
        emb_server_cfg = emb_cfg.get("server", {}) or {}
        cfg.emb_server_path = _resolve_server_path(str(emb_server_cfg.get("path", DEFAULT_SERVER_REL)))
        cfg.emb_server_host = str(emb_server_cfg.get("host", cfg.emb_server_host))
        cfg.emb_server_port = int(emb_server_cfg.get("port", cfg.emb_server_port))
        cfg.emb_server_timeout = int(emb_server_cfg.get("start_timeout", cfg.emb_server_timeout))
        emb_model_name = str(emb_cfg.get("model", ""))
        cfg.emb_model_path = cfg.models_dir / emb_model_name if emb_model_name else None
        cfg.emb_ctx = int(emb_cfg.get("ctx", cfg.emb_ctx))
        cfg.emb_batch = int(emb_cfg.get("batch", cfg.emb_batch))
        cfg.emb_threads = int(emb_cfg.get("threads", cfg.emb_threads))

        # 视觉 llama-server 配置
        vis_cfg = (llm_cfg.get("vision", {}) or {})
        vis_server_cfg = vis_cfg.get("server", {}) or {}
        cfg.vis_server_path = _resolve_server_path(str(vis_server_cfg.get("path", DEFAULT_SERVER_REL)))
        cfg.vis_server_host = str(vis_server_cfg.get("host", cfg.vis_server_host))
        cfg.vis_server_port = int(vis_server_cfg.get("port", cfg.vis_server_port))
        cfg.vis_server_timeout = int(vis_server_cfg.get("start_timeout", cfg.vis_server_timeout))
        vis_model_name = str(vis_cfg.get("model", ""))
        cfg.vis_model_path = cfg.models_dir / vis_model_name if vis_model_name else None
        mmproj_name = str(vis_cfg.get("mmproj", ""))
        cfg.vis_mmproj_path = cfg.models_dir / mmproj_name if mmproj_name else None
        cfg.vis_ctx = int(vis_cfg.get("ctx", cfg.vis_ctx))
        cfg.vis_batch = int(vis_cfg.get("batch", cfg.vis_batch))
        cfg.vis_threads = int(vis_cfg.get("threads", cfg.vis_threads))
        cfg.vis_lazy = bool(vis_cfg.get("lazy", cfg.vis_lazy))

        # 档位覆盖：每个档位可指定自己的视觉模型与懒启动策略（8GB 分界两套模型）
        profile_vis = p.get("vision", {}) or {}
        if profile_vis.get("model"):
            cfg.vis_model_path = cfg.models_dir / str(profile_vis["model"])
        if profile_vis.get("lazy") is not None:
            cfg.vis_lazy = bool(profile_vis.get("lazy"))

        # 记忆检索配置
        mem_cfg = raw.get("memory", {}) or {}
        cfg.memory_top_k = int(mem_cfg.get("top_k", cfg.memory_top_k))
        cfg.memory_min_score = float(mem_cfg.get("min_score", cfg.memory_min_score))
        cfg.memory_auto_remember = bool(mem_cfg.get("auto_remember_chat", cfg.memory_auto_remember))
        cfg.memory_dedup_threshold = float(mem_cfg.get("dedup_threshold", cfg.memory_dedup_threshold))

        # 采集
        col_cfg = raw.get("collector", {}) or {}
        cfg.collector_enabled = bool(col_cfg.get("enabled", cfg.collector_enabled))
        cfg.monitor_mode = str(col_cfg.get("monitor", cfg.monitor_mode))
        cfg.locked_pattern = str(col_cfg.get("locked_pattern", cfg.locked_pattern))
        cfg.monitor_interval = int(col_cfg.get("interval_s", cfg.monitor_interval))
        cfg.start_paused = bool(col_cfg.get("start_paused", cfg.start_paused))
        cfg.watch_dirs = list(col_cfg.get("watch_dirs", cfg.watch_dirs) or [])
        cfg.clipboard_enabled = bool(col_cfg.get("clipboard", cfg.clipboard_enabled))
        cfg.browser_history = bool(col_cfg.get("browser_history", cfg.browser_history))
        cfg.git_enabled = bool(col_cfg.get("git", cfg.git_enabled))
        cfg.batch_minutes = int(col_cfg.get("batch_minutes", cfg.batch_minutes))
        cfg.screenshot_policy = str(col_cfg.get("screenshot_policy", cfg.screenshot_policy))
        cfg.agent_apps = list(col_cfg.get("agent_apps", cfg.agent_apps) or [])

        # 任务引擎
        te_cfg = raw.get("task_engine", {}) or {}
        cfg.blocked_persist_batches = int(te_cfg.get("blocked_persist_batches", cfg.blocked_persist_batches))
        cfg.solved_min_signals = int(te_cfg.get("solved_min_signals", cfg.solved_min_signals))
        cfg.focus_bullet_cap = int(te_cfg.get("focus_bullet_cap", cfg.focus_bullet_cap))
        cfg.compaction_batch = int(te_cfg.get("compaction_batch", cfg.compaction_batch))
        cfg.hot_keywords = list(te_cfg.get("hot_keywords", cfg.hot_keywords) or [])
        cfg.recall_minutes = int(te_cfg.get("recall_minutes", cfg.recall_minutes))
        cfg.recall_threshold = float(te_cfg.get("recall_threshold", cfg.recall_threshold))

        # 桌面层
        desk_cfg = raw.get("desktop", {}) or {}
        cfg.desktop_enabled = bool(desk_cfg.get("enabled", cfg.desktop_enabled))
        cfg.bubble_enabled = bool(desk_cfg.get("bubble", cfg.bubble_enabled))
        cfg.tray_enabled = bool(desk_cfg.get("tray", cfg.tray_enabled))
        cfg.toasts_enabled = bool(desk_cfg.get("toasts", cfg.toasts_enabled))
        cfg.chat_window_mode = str(desk_cfg.get("chat_window", cfg.chat_window_mode))

        # 巩固
        con_cfg = raw.get("consolidation", {}) or {}
        cfg.short_retention_hours = int(con_cfg.get("short_retention_hours", cfg.short_retention_hours))
        cfg.event_retention_days = int(con_cfg.get("event_retention_days", cfg.event_retention_days))
        cfg.trash_days = int(con_cfg.get("trash_days", cfg.trash_days))
        cfg.caps = dict(con_cfg.get("caps", cfg.caps) or {})
        cfg.consolidation_daily = str((con_cfg.get("schedule", {}) or {}).get("daily", cfg.consolidation_daily))

        # llm 手动覆盖
        if llm_cfg.get("model_path"):
            cfg.model_path = Path(str(llm_cfg["model_path"]))
        if llm_cfg.get("n_ctx"):
            cfg.n_ctx = int(llm_cfg["n_ctx"])
        if llm_cfg.get("n_threads"):
            cfg.n_threads = int(llm_cfg["n_threads"])
        if llm_cfg.get("n_batch"):
            cfg.n_batch = int(llm_cfg["n_batch"])
        return cfg

    def describe(self) -> str:
        ram = detect_ram_gb()
        model = self.model_path.name if self.model_path else "未配置"
        return (
            f"档位={self.profile} (探测={self.profile_detected}) | 内存={ram:.1f}GB | "
            f"线程={self.n_threads} | ctx={self.n_ctx} batch={self.n_batch} | 模型={model}"
        )
