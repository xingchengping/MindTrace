"""Personal Cognitive OS 入口：python main.py → http://127.0.0.1:4000"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import uvicorn

from app.core.paths import EXE_DIR, RESOURCE_DIR, is_frozen

if is_frozen():
    sys.path.insert(0, str(RESOURCE_DIR))

from app.api.chat import router as chat_router
from app.api.graph import router as graph_router
from app.api.memory import router as memory_router
from app.api.settings import router as settings_router
from app.collector.monitor import MonitorService
from app.core.config import AppConfig
from app.core.database import init_db
from app.core.logging import setup_logging
from app.desktop.notify import NotifyService
from app.graph.builder import GraphBuilder
from app.graph.store import GraphStore
from app.llm.engine import LlamaServer
from app.memory.consolidation import ConsolidationService
from app.memory.service import MemoryService
from app.memory.vectors import VectorStore
from app.tasks.engine import TaskEngine
from app.vision import VisionProcessor

WEB_DIR = RESOURCE_DIR / "app" / "web"


def build_app(cfg: AppConfig) -> FastAPI:
    app = FastAPI(title="Personal Cognitive OS", version="0.2.0")

    @app.middleware("http")
    async def no_cache_api(request, call_next):
        """API 响应一律不缓存。

        否则 Chromium 会对无缓存头的 GET 做启发式缓存，第二次请求相同 URL 时
        发条件验证请求，在 QtWebEngine（弹窗内嵌页面）里会挂起，导致会话切换
        等重复请求卡死。
        """
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health():
        llm: LlamaServer | None = app.state.llm
        emb: LlamaServer | None = app.state.embedding
        memory: MemoryService | None = app.state.memory
        return {
            "status": "ok" if (llm and llm.ready) else "llm_unavailable",
            "profile": cfg.profile,
            "profile_detected": cfg.profile_detected,
            "model": cfg.model_path.name if cfg.model_path else None,
            "llm_ready": bool(llm and llm.ready),
            "llm_error": llm.error if llm else None,
            "embedding_ready": bool(emb and emb.ready),
            "embedding_model": cfg.emb_model_path.name if cfg.emb_model_path else None,
            "memory_ready": bool(memory and memory.enabled),
            "vector_count": app.state.vectors.count() if app.state.vectors else 0,
            "collector": bool(app.state.monitor is not None),
            "collector_paused": bool(getattr(app.state, "collector_paused", False)),
            "task_engine": bool(app.state.task_engine is not None),
            "n_ctx": cfg.n_ctx,
            "n_threads": cfg.n_threads,
        }

    app.include_router(chat_router, prefix="/api")
    app.include_router(memory_router)
    app.include_router(settings_router)
    app.include_router(graph_router)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/chat")
    def chat_window():
        """独立对话窗页面（悬浮球左键打开）。"""
        return FileResponse(WEB_DIR / "chat.html")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Personal Cognitive OS")
    parser.add_argument("--port", type=int, default=None, help="Web 端口（默认 4000）")
    parser.add_argument(
        "--profile", choices=["auto", "light", "standard", "powerful"], default=None,
        help="硬件档位（默认 auto 探测）",
    )
    parser.add_argument(
        "--no-server", action="store_true",
        help="不自动启动 llama-server（假设已外部启动）",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="不启动桌面层（悬浮球/托盘），仅 Web 服务（测试用）",
    )
    parser.add_argument(
        "--no-task-engine", action="store_true",
        help="不启动 TaskEngine 调度器（测试用，避免双引擎竞争）",
    )
    parser.add_argument(
        "--download-models", action="store_true",
        help="下载当前档位所需模型后退出（exe 内置入口，无需 Python）",
    )
    args = parser.parse_args()

    cfg = AppConfig.load(profile=args.profile)  # profile 在加载时解析，档位相关设置（模型/上下文/视觉懒启动）正确生效
    if args.port:
        cfg.port = args.port

    if args.download_models:
        from app.core.downloader import download_profile_models
        ok = download_profile_models(cfg)
        print("模型下载" + ("完成。" if ok else "部分失败，可重试。"))
        sys.exit(0 if ok else 1)

    setup_logging(cfg.log_level, cfg.data_dir / "logs")
    engine, SessionLocal = init_db(cfg.data_dir)

    print("=" * 64)
    print("  MindTrace · Personal Cognitive OS (Phase 2)")
    print(f"  {cfg.describe()}")
    print("=" * 64)

    # ---- 对话 LLM ----
    llm = LlamaServer(
        server_path=cfg.server_path, model_path=cfg.model_path,
        host=cfg.server_host, port=cfg.server_port,
        n_ctx=cfg.n_ctx, n_threads=cfg.n_threads, n_batch=cfg.n_batch,
        n_parallel=cfg.n_parallel,
        log_path=cfg.data_dir / "logs" / "llama-server.log",
    )
    if args.no_server:
        llm.attach_external(wait_seconds=15)
    else:
        llm.start(wait_seconds=cfg.server_timeout)
    if not llm.ready:
        print(f"[llm] {llm.error}")

    # ---- Embedding LLM ----
    embedding = LlamaServer(
        server_path=cfg.emb_server_path, model_path=cfg.emb_model_path,
        host=cfg.emb_server_host, port=cfg.emb_server_port,
        n_ctx=cfg.emb_ctx, n_threads=cfg.emb_threads, n_batch=cfg.emb_batch,
        log_path=cfg.data_dir / "logs" / "embedding-server.log",
        mode="embedding",
    )
    emb_ok = embedding.start(wait_seconds=cfg.emb_server_timeout)
    if not emb_ok:
        print(f"[emb] {embedding.error}")

    # ---- Vision LLM（截图理解；light 档懒启动，模型缺失时跳过） ----
    vision = LlamaServer(
        server_path=cfg.vis_server_path, model_path=cfg.vis_model_path,
        host=cfg.vis_server_host, port=cfg.vis_server_port,
        n_ctx=cfg.vis_ctx, n_threads=cfg.vis_threads, n_batch=cfg.vis_batch,
        log_path=cfg.data_dir / "logs" / "vision-server.log",
        mode="vision", mmproj_path=cfg.vis_mmproj_path,
    )
    vis_models_ready = bool(
        cfg.vis_model_path and cfg.vis_model_path.exists()
        and cfg.vis_mmproj_path and cfg.vis_mmproj_path.exists()
    )
    vis_ok = False
    if not vis_models_ready:
        print("[vis] 视觉模型未下载（截图理解跳过；运行 scripts/download_models.py --model vision 启用）")
    elif cfg.vis_lazy:
        print(f"[vis] {cfg.profile} 档：视觉按需懒启动（{cfg.vis_model_path.name}，有截图待理解时才启动）")
    else:
        vis_ok = vision.start(wait_seconds=cfg.vis_server_timeout)
        if vis_ok:
            print(f"[vis] 视觉服务已就绪（{cfg.vis_model_path.name}）")
        else:
            print(f"[vis] {vision.error}")

    # ---- 记忆系统 ----
    vectors = VectorStore(engine)
    memory = MemoryService(
        embedding=embedding if emb_ok else None, vectors=vectors,
        session_factory=SessionLocal, top_k=cfg.memory_top_k,
        min_score=cfg.memory_min_score, dedup_threshold=cfg.memory_dedup_threshold,
        llm=llm if llm.ready else None,   # HyDE：复用聊天 LLM，不占额外模型内存
    )
    if memory.enabled:
        print(f"[mem] 记忆已启用（向量库 {vectors.count()} 条）")

    # ---- 图谱 ----
    graph = GraphStore(SessionLocal)
    graph.load()
    graph_builder = GraphBuilder(SessionLocal, graph)

    # ---- 通知 / 任务引擎 / 采集 / 巩固 ----
    from app.core.settings_store import get_setting
    db0 = SessionLocal()
    try:
        quiet_start = get_setting(db0, "quiet_start", "23:00")
        quiet_end = get_setting(db0, "quiet_end", "07:00")
    finally:
        db0.close()
    notify = NotifyService(SessionLocal, toasts_enabled=cfg.toasts_enabled and cfg.desktop_enabled,
                           quiet_start=quiet_start, quiet_end=quiet_end)
    task_engine = TaskEngine(cfg, SessionLocal, memory, llm if llm.ready else None, notify)
    task_engine.set_paused(bool(cfg.collector_enabled and cfg.start_paused))  # 初始与采集暂停状态一致
    monitor = MonitorService(cfg, SessionLocal, memory, llm if llm.ready else None)
    consolidation = ConsolidationService(cfg, SessionLocal, vectors, llm=llm if llm.ready else None,
                                         memory=memory, notify=notify, graph_store=graph)
    vision_processor = VisionProcessor(cfg, SessionLocal,
                                       vision if (vis_ok or (cfg.vis_lazy and vis_models_ready)) else None,
                                       memory)

    if llm.ready and not args.no_task_engine:
        task_engine.start()
    if cfg.collector_enabled:
        monitor.start()

    # 默认暂停采集（config collector.start_paused）：右键菜单显示"开始采集"，用户主动点击开始后才采集
    if cfg.collector_enabled and cfg.start_paused:
        try:
            monitor.set_paused(True)
        except Exception:  # noqa: BLE001
            pass

    # ---- Web 应用 ----
    app = build_app(cfg)
    app.state.llm = llm
    app.state.embedding = embedding
    app.state.vectors = vectors
    app.state.memory = memory
    app.state.notify = notify
    app.state.task_engine = task_engine
    app.state.monitor = monitor
    app.state.consolidation = consolidation
    app.state.SessionLocal = SessionLocal
    app.state.cfg = cfg
    app.state.collector_paused = bool(cfg.collector_enabled and cfg.start_paused)
    app.state._quit_requested = False
    app.state.graph = graph
    app.state.graph_builder = graph_builder
    app.state.vision = vision
    app.state.vision_processor = vision_processor

    # 巩固调度
    from apscheduler.schedulers.background import BackgroundScheduler
    from datetime import datetime, timedelta

    def _consolidation_catchup():
        """启动后补跑巩固/图谱同步。

        经验只能由"每日 02:30"定时任务产出——应用不在运行就永远不会执行；
        短会话（<1h）也等不到每小时任务。补跑让任何一次启动都能产出经验/图谱。
        """
        if not (llm and llm.ready):
            print("[mem] 巩固补跑跳过：模型未就绪")
            return
        for name, fn in (("hourly", consolidation.hourly),
                         ("episodic", consolidation.episodic),
                         ("daily", consolidation.daily),
                         ("graph", graph_builder.sync_all)):
            try:
                stats = fn()
                if stats:
                    print(f"[mem] 巩固补跑 {name}: {stats}")
            except Exception as exc:  # noqa: BLE001
                print(f"[mem] 巩固补跑 {name} 失败: {exc}")

    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(consolidation.hourly, "interval", hours=1, id="cons_hourly", max_instances=1)
    sched.add_job(consolidation.episodic, "interval", hours=2, id="cons_episodic", max_instances=1)
    try:
        hh, mm = (int(x) for x in cfg.consolidation_daily.split(":"))
    except Exception:
        hh, mm = 2, 30
    sched.add_job(consolidation.daily, "cron", hour=hh, minute=mm, id="cons_daily", max_instances=1)
    sched.add_job(graph_builder.sync_all, "interval", minutes=10, id="graph_sync", max_instances=1)
    sched.add_job(vision_processor.process_pending, "interval", minutes=15, id="vision", max_instances=1)
    sched.add_job(_consolidation_catchup, "date",
                  run_date=datetime.now() + timedelta(seconds=120), id="cons_catchup")
    sched.start()

    print(f"\n  界面：http://{cfg.host}:{cfg.port}")
    print("  按 Ctrl+C 退出（同时关闭 llama-server）\n")

    # ---- 运行 ----
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level=cfg.log_level.lower())
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    def shutdown_all():
        server.should_exit = True
        try:
            sched.shutdown(wait=False)
        except Exception:  # noqa: BLE001  # 可能已被关闭
            pass
        monitor.stop()
        task_engine.stop()
        llm.stop()
        embedding.stop()
        vision.stop()

    bubble = None
    glow = None
    if cfg.desktop_enabled and not args.headless:
        from app.desktop.tray import run_tray

        def on_pause(paused: bool):
            app.state.collector_paused = paused
            # 调度级暂停：任务直接不执行，不再空跑刷日志
            try:
                monitor.set_paused(paused)
            except Exception:  # noqa: BLE001
                pass
            # 采集暂停 → 任务引擎自动分析也停摆（聊天/手动信号不受影响）
            try:
                task_engine.set_paused(paused)
            except Exception:  # noqa: BLE001
                pass
            # 开始采集（暂停 → 采集）→ 全屏流光 + 唤醒音效；暂停采集 → 不出现（隐藏）
            if glow is not None:
                try:
                    if paused:
                        glow.hide_glow()
                    else:
                        glow.show_glow()
                except Exception:  # noqa: BLE001
                    pass

        if cfg.bubble_enabled:
            # Qt（Chromium 果冻球）优先，不可用时回退 Tk 版
            try:
                from app.desktop.bubble_qt import BubbleQt

                bubble = BubbleQt(f"http://{cfg.host}:{cfg.port}", on_pause=on_pause, on_quit=shutdown_all)
            except Exception as exc:  # pragma: no cover
                print(f"[desktop] Qt 悬浮球不可用（{exc}），回退 Tk 版")
                bubble = None
            if bubble is None:
                try:
                    from app.desktop.bubble import Bubble

                    bubble = Bubble(f"http://{cfg.host}:{cfg.port}", on_pause=on_pause, on_quit=shutdown_all)
                except Exception as exc:  # pragma: no cover
                    print(f"[desktop] 悬浮球启动失败: {exc}")
                    bubble = None
        if cfg.tray_enabled:
            run_tray(f"http://{cfg.host}:{cfg.port}", on_quit=shutdown_all)

        # 全屏流光特效（仅 Qt 桌面模式；点击"开始采集"时由 on_pause 触发，播放唤醒 Chime）
        qt_bubble = False
        try:
            qt_bubble = isinstance(bubble, BubbleQt)
        except NameError:  # Qt bubble 导入失败回退 Tk 时 BubbleQt 未定义
            qt_bubble = False
        if cfg.bubble_enabled and qt_bubble and cfg.collector_enabled:
            try:
                from app.desktop.glow import GlowOverlay

                glow = GlowOverlay()
                print("[desktop] 全屏流光特效已就绪（点击'开始采集'时显示 + 唤醒音效）")
            except Exception as exc:  # pragma: no cover
                print(f"[desktop] 流光特效不可用（{exc}）")
                glow = None

        if bubble is not None:
            try:
                bubble.run()  # Qt 或 Tk 主循环（主线程）
            except Exception as exc:  # pragma: no cover
                print(f"[desktop] 悬浮球异常退出: {exc}")

    try:
        while not getattr(app.state, "_quit_requested", False):
            if not server_thread.is_alive():
                break
            server_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_all()


if __name__ == "__main__":
    main()
