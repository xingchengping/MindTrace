"""桌面悬浮球（Qt + QWebEngineView）—— 果冻小球 HTML 由 Chromium 引擎渲染。

架构（按用户方案）：
  - 无边框、透明、置顶的 Qt 窗口承载 QWebEngineView
  - 果冻球的所有动画（渐变/光晕/轻浮/拖动拉伸/弹性回弹）完全由 HTML/CSS/JS 驱动
  - JS 通过 QWebChannel 调用 Python（window.py.*）：
      moveWindowBy(dx, dy) → 移动整个窗口（桌面悬浮球 = 窗口移动）
      onClick()            → 左键单击：切换锚定对话小窗
      onRightClick()       → 右键：功能菜单
      onDragEnd()          → 拖动结束（当前无额外处理）
  - Python → JS：角标更新（runJavaScript setBadge(n)），30s 轮询 /api/notifications/count

功能与 Tk 版完全一致：左键对话小窗、右键菜单（网页端/待确认/监控目标/暂停/免打扰/设置/退出）、角标。
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import urllib.request
import webbrowser

# 必须在 PySide6 导入前：统一环境配置（沙箱/软件渲染/便携缓存）+ 预载 ICU
from app.desktop.qt_compat import preload_qt  # noqa: E402,F401

preload_qt()

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from app.core.paths import RESOURCE_DIR
from app.desktop.popover_qt import JellyPopover

log = logging.getLogger("mindtrace.desktop.bubble_qt")

WIN_W, WIN_H = 200, 200          # 窗口尺寸（球 120 + 光晕余量）
HTML_PATH = RESOURCE_DIR / "app" / "desktop" / "bubble.html"


class BubbleBridge(QObject):
    """暴露给 JS 的 Python 桥（QWebChannel object 'py'）。"""

    moveSignal = Signal(float, float)
    clickSignal = Signal()
    rightClickSignal = Signal()
    dragStartSignal = Signal()
    dragEndSignal = Signal()
    badgeSignal = Signal(int)

    @Slot(float, float)
    def moveWindowBy(self, dx: float, dy: float):
        self.moveSignal.emit(dx, dy)

    @Slot()
    def onClick(self):
        self.clickSignal.emit()

    @Slot()
    def onRightClick(self):
        self.rightClickSignal.emit()

    @Slot()
    def onDragStart(self):
        self.dragStartSignal.emit()

    @Slot()
    def onDragEnd(self):
        self.dragEndSignal.emit()


class BubbleQt:
    """Qt 果冻球悬浮球（QApplication 主循环，与 Tk 版同接口：run()/stop()）。"""

    def __init__(self, base_url: str, on_pause=None, on_quit=None):
        self.base_url = base_url.rstrip("/")
        self.on_pause = on_pause
        self.on_quit = on_quit
        self._paused = True   # 默认暂停采集（菜单显示"开始采集"；服务端 start_paused 一致）
        self._popover: JellyPopover | None = None

        self.app = QApplication.instance() or QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # 无边框透明置顶窗口（Tool 隐藏任务栏）
        self.win = QWidget()
        self.win.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.win.setFixedSize(WIN_W, WIN_H)
        self.win.move(120, 120)

        # Chromium 视图（页面透明）
        self.view = QWebEngineView(self.win)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.resize(WIN_W, WIN_H)
        page = self.view.page()
        page.setBackgroundColor(QColor(0, 0, 0, 0))

        # JS 桥
        self.bridge = BubbleBridge()
        self.channel = QWebChannel(page)
        self.channel.registerObject("py", self.bridge)
        page.setWebChannel(self.channel)

        self.bridge.moveSignal.connect(self._on_move)
        self.bridge.clickSignal.connect(self._toggle_popover)
        self.bridge.rightClickSignal.connect(self._show_menu)
        self.bridge.badgeSignal.connect(self._set_badge_js)

        # 加载果冻球页面
        # 注意：必须用 http 基址（setHtml），file:// 页面下 Chromium 会拦截 qrc:///
        # 的 qwebchannel.js，导致 QWebChannel 未定义、整段 JS 中断。
        try:
            html_text = HTML_PATH.read_text(encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            log.warning("读取果冻球页面失败: %s", exc)
            raise
        page.setHtml(html_text, QUrl("http://localhost/"))
        page.loadFinished.connect(lambda ok: self._on_loaded(ok))

        self.win.show()

        # 待确认角标轮询（30s，GUI 线程定时器 + 工作线程拉取）
        self._badge_timer = QTimer(self.win)
        self._badge_timer.timeout.connect(self._poll_badge_once)
        self._badge_timer.start(30000)
        self._poll_badge_once()

    # ---------- JS 桥回调 ----------

    def _on_move(self, dx: float, dy: float):
        # 真正开始拖动时关闭已打开的对话小窗（位置会过期）。
        # 注意不能在“按下”时关（onDragStart）：否则点击关闭弹窗会变成
        # “关闭→立即重开”，永远关不掉。
        if (dx or dy) and self._popover is not None:
            self._close_popover()
        x = self.win.x() + round(dx)
        y = self.win.y() + round(dy)
        self.win.move(x, y)

    def _on_loaded(self, ok: bool):
        # 加载完成后重申透明背景，避免首帧白闪
        self.view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        if not ok:
            log.warning("果冻球页面加载失败: %s", HTML_PATH)

    # ---------- 对话小窗 ----------

    def _close_popover(self):
        """按下时先关闭已打开的对话小窗（拖动后位置会过期）。"""
        if self._popover is not None:
            try:
                self._popover.close()
            except Exception:  # pragma: no cover
                pass
            self._popover = None

    def _toggle_popover(self):
        if self._popover is not None and self._popover.isVisible():
            self._popover.close()
            self._popover = None
            return
        try:
            self._popover = JellyPopover(self.win.frameGeometry(), self.base_url, parent=None)
            self._popover.show()
        except Exception as exc:  # pragma: no cover
            log.warning("对话小窗打开失败: %s", exc)

    # ---------- 右键菜单 ----------

    def _monitor_state(self) -> dict:
        """取当前监控模式与暂停状态（供菜单文案/对勾；失败用本地值兜底）。"""
        try:
            req = urllib.request.Request(self.base_url + "/api/monitor")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return {
                "mode": data.get("mode", "follow"),
                "pattern": data.get("pattern", "") or "",
                "paused": bool(data.get("paused", False)),
            }
        except Exception:  # noqa: BLE001
            return {"mode": "follow", "pattern": "", "paused": self._paused}

    def _show_menu(self):
        state = self._monitor_state()
        mode, pattern, paused = state["mode"], state["pattern"], state["paused"]

        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background:#26262a; color:#f5f5f7; border:1px solid #3a3a40; "
            "border-radius:8px; padding:6px; font-size:13px; }"
            "QMenu::item { padding:6px 28px 6px 14px; border-radius:5px; }"
            "QMenu::item:selected { background:#0a84ff; color:#fff; }"
            "QMenu::separator { height:1px; background:#3a3a40; margin:5px 8px; }"
        )
        menu.addAction("打开网页端", lambda: webbrowser.open(self.base_url))
        menu.addAction("待确认经验", lambda: webbrowser.open(self.base_url + "/#view-experiences"))

        # 监控目标子菜单（当前项打 ✓）
        sub = menu.addMenu("🎯 监控目标")
        act_follow = sub.addAction("跟随前台窗口（默认）", lambda: self._set_monitor("follow", ""))
        act_follow.setCheckable(True)
        act_follow.setChecked(mode == "follow")
        sub.addSeparator()
        try:
            from app.collector.window import list_open_windows
            windows = list_open_windows(limit=30)
        except Exception:  # noqa: BLE001
            windows = []
        if mode == "locked" and pattern:
            # 锁定中的窗口若不在当前列表（已关闭/标题变化），单独显示，保证能看到当前锁的是谁
            matched = any(
                (pattern.lower() in (w.get("title", "") or "").lower())
                or ((w.get("title", "") or "").lower() in pattern.lower())
                for w in windows
            )
            if not matched:
                act_lock = sub.addAction("🔒 锁定中：" + pattern[:42])
                act_lock.setCheckable(True)
                act_lock.setChecked(True)
                sub.addSeparator()
        if windows:
            for w in windows:
                label = ((w.get("app", "") + " — ") if w.get("app") else "") + w.get("title", "")
                act = sub.addAction("🔒 " + label[:42], lambda t=w.get("title", ""): self._set_monitor("locked", t))
                act.setCheckable(True)
                # 子串双向匹配：窗口标题变化（如加"已修改"后缀）也能认出当前锁定的窗口
                title = w.get("title", "") or ""
                act.setChecked(
                    mode == "locked" and pattern and (
                        pattern.lower() in title.lower() or title.lower() in pattern.lower()
                    )
                )
        else:
            act = sub.addAction("（暂无可选窗口）")
            act.setEnabled(False)

        menu.addSeparator()
        menu.addAction("开始采集" if paused else "暂停采集", self._toggle_pause)
        menu.addAction("免打扰时段…", lambda: webbrowser.open(self.base_url + "/#view-settings"))
        menu.addAction("设置", lambda: webbrowser.open(self.base_url + "/#view-settings"))
        menu.addSeparator()
        menu.addAction("退出", self._quit)
        menu.exec(QCursor.pos())

    def _set_monitor(self, mode: str, pattern: str):
        """热更新监控目标（无需重启）。"""
        try:
            body = json.dumps({"mode": mode, "pattern": pattern}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self.base_url + "/api/monitor", data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            log.info("监控目标已切换: %s %s", mode, pattern)
        except Exception as exc:  # noqa: BLE001
            log.warning("设置监控目标失败: %s", exc)

    def _toggle_pause(self):
        """以服务端状态为准取反（菜单文案与动作一致）：开始采集 → on_pause(False)；暂停 → on_pause(True)。"""
        state = self._monitor_state()
        self._paused = not bool(state["paused"])
        try:
            if self.on_pause:
                self.on_pause(self._paused)
        except Exception:  # noqa: BLE001
            pass

    def _quit(self):
        if self.on_quit:
            try:
                self.on_quit()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.app.quit()
        except Exception:  # noqa: BLE001
            pass

    # ---------- 角标 ----------

    def _poll_badge_once(self):
        def work():
            try:
                req = urllib.request.Request(self.base_url + "/api/notifications/count")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                count = int(data.get("pending", 0))
                self.bridge.badgeSignal.emit(count)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=work, daemon=True).start()

    def _set_badge_js(self, count: int):
        try:
            self.view.page().runJavaScript(f"window.setBadge && window.setBadge({count})")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 生命周期 ----------

    def run(self):
        return self.app.exec()

    def stop(self):
        try:
            self.app.quit()
        except Exception:  # noqa: BLE001
            pass
