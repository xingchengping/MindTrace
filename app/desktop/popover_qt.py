"""锚定悬浮球的对话小窗（Qt 内嵌网页端聊天页 /chat）。

直接加载 app/web/chat.html（路由 /chat）—— 与网页端聊天框 100% 同款：
圆角气泡、左右分列、编辑/删除、流式输出、共享最近会话。
Qt 侧只负责：无边框半透明圆角面板 + 指向悬浮球的箭头 + 页面透明化。
关闭方式：再点悬浮球 / Esc。
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPainterPath, QShortcut
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

from app.desktop.qt_compat import preload_qt

preload_qt()

log = logging.getLogger("mindtrace.desktop.popover_qt")

W, H = 380, 440
GAP = 10
RADIUS = 16
PANEL_FALLBACK = "#1d1d1f"   # 页面加载前先用深色，加载后按页面 --bg 同步


class _PopoverBridge(QObject):
    """暴露给聊天页的桥：关闭弹窗 + 语音问答（录音/转写/合成/循环对话）。"""

    closeSignal = Signal()
    voiceResultSignal = Signal(str)   # 转写结果 → 页面（单次填充 / 循环推进）
    speakDoneSignal = Signal()        # 播报完成 → 页面（循环对话开始下一轮）

    @Slot()
    def closePopover(self):
        self.closeSignal.emit()

    @Slot()
    def voiceInput(self):
        """录音（静音检测说完即停）+ Vosk 转写（工作线程，避免卡 GUI）。"""
        def work():
            try:
                from app.voice import available, record, transcribe

                if not available():
                    self.voiceResultSignal.emit("")
                    return
                wav = record(duration=15.0, silence_timeout=1.2)
                if not wav:
                    self.voiceResultSignal.emit("")
                    return
                text = transcribe(wav)
                self.voiceResultSignal.emit(text or "")
            except Exception:  # noqa: BLE001
                self.voiceResultSignal.emit("")

        threading.Thread(target=work, daemon=True).start()

    @Slot(str)
    def speak(self, text: str):
        """Piper1 语音合成播报（工作线程）；播报完成后发 speakDoneSignal（循环对话用）。"""
        def work():
            try:
                from app.voice import speak as _speak

                _speak(text)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.speakDoneSignal.emit()

        if text:
            threading.Thread(target=work, daemon=True).start()
        else:
            self.speakDoneSignal.emit()


def _screen_work_area(cx: int, cy: int):
    """取悬浮球所在显示器的可用区域（多显示器正确）。"""
    screen = QGuiApplication.screenAt(QPoint(cx, cy)) or QGuiApplication.primaryScreen()
    return screen.availableGeometry()


def _anchor_position(anchor_rect: QRect) -> tuple[int, int, str]:
    """展开方向：默认向右；仅当气泡贴近屏幕右缘（右边放不下弹窗）才向左。

    垂直方向只做居中对齐与屏幕内钳制，不参与方向选择。
    """
    bx, by = anchor_rect.x(), anchor_rect.y()
    bw, bh = anchor_rect.width(), anchor_rect.height()
    work = _screen_work_area(bx + bw // 2, by + bh // 2)
    wl, wt = work.x(), work.y()
    wr, wb = work.right(), work.bottom()

    # 默认向右；右侧剩余空间不足以放下弹窗时向左
    room_right = wr - (bx + bw + GAP + W)
    direction = "right" if room_right < 0 else "left"

    # 水平展开：垂直居中对齐气泡中心
    y = by + bh // 2 - H // 2
    y = max(wt + 6, min(y, wb - H - 6))
    if direction == "left":     # 默认 → 向右展开
        x = bx + bw + GAP
    else:                       # 右缘 → 向左展开
        x = bx - W - GAP
    x = max(wl + 6, min(x, wr - W - 6))
    y = max(wt + 6, min(y, wb - H - 6))
    return x, y, direction


class JellyPopover(QWidget):
    """紧贴悬浮球的迷你对话窗（内嵌网页聊天页）。"""

    def __init__(self, anchor_rect: QRect, base_url: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.base_url = base_url.rstrip("/")
        self._panel_color = QColor(PANEL_FALLBACK)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        x, y, self.direction = _anchor_position(anchor_rect)
        self.move(x, y)

        # 网页聊天视图（透明页面，圆角面板/箭头由本窗口绘制透出）
        self.view = QWebEngineView(self)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.view.resize(W, H)
        page = self.view.page()
        page.setBackgroundColor(QColor(0, 0, 0, 0))

        # 桥（页面内 ✕ 关闭 + 语音问答；语音信号由页面 JS 侧统一 connect，避免双触发）
        self._bridge = _PopoverBridge()
        self._bridge.closeSignal.connect(self.close)
        self._channel = QWebChannel(page)
        self._channel.registerObject("py", self._bridge)
        page.setWebChannel(self._channel)

        # 共享最近会话：在 chat.js 执行前写入 localStorage（DocumentCreation 注入）
        session_id = self._recent_session_id()
        if session_id:
            page.scripts().insert(self._seed_script(session_id))
        # 首帧前样式注入（透明化/细滚动条/关毛玻璃）
        page.scripts().insert(self._styles_script())

        page.loadFinished.connect(self._on_loaded)
        page.load(QUrl(self.base_url + "/chat"))

        # Esc 关闭
        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc.activated.connect(self.close)

    # ---------- 会话共享 ----------

    def _recent_session_id(self) -> str | None:
        """取最近会话（与网页端同步）。"""
        try:
            req = urllib.request.Request(self.base_url + "/api/chat/sessions")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            sessions = data.get("sessions") or []
            if sessions:
                return sessions[0]["client_id"]
        except Exception as exc:  # noqa: BLE001
            log.debug("读取最近会话失败: %s", exc)
        return None

    @staticmethod
    def _seed_script(session_id: str) -> QWebEngineScript:
        """文档创建时写入 localStorage.mt_session（早于 chat.js 的 init）。

        仅在本地无记忆时注入——这样用户在弹窗里切换过的会话会被记住，
        不会被最近会话覆盖。
        """
        s = QWebEngineScript()
        s.setName("mt_seed_session")
        s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        s.setSourceCode(
            "if(!localStorage.getItem('mt_session')){"
            "localStorage.setItem('mt_session', '" + session_id + "');"
            "}"
        )
        return s

    @staticmethod
    def _styles_script() -> QWebEngineScript:
        """首帧前注入弹窗页面样式（DocumentCreation + DOMContentLoaded）。

        - 页面背景透明化（圆角面板/箭头透出）
        - 关毛玻璃模糊（纯色面板上无视觉作用，纯耗性能）
        - 细滚动条（默认宽滚动条会盖住右侧消息与按钮）
        - 消息区右内边距（气泡不贴滚动条）
        """
        css = (
            'html,body,body[data-mode="chat"]{background:transparent!important}'
            "*{backdrop-filter:none!important;-webkit-backdrop-filter:none!important}"
            "::-webkit-scrollbar{width:6px;height:6px}"
            "::-webkit-scrollbar-thumb{background:rgba(128,128,128,0.35);border-radius:3px}"
            "::-webkit-scrollbar-track{background:transparent}"
            "::-webkit-scrollbar-corner{background:transparent}"
            "#messages{padding-right:14px}"
            # flex 项允许收缩：长不可断内容（URL/路径等）不会把布局撑宽导致右缘被裁
            ".chat-window>*{min-width:0}.msg{min-width:0}.bubble{min-width:0;overflow-wrap:anywhere}"
        )
        s = QWebEngineScript()
        s.setName("mt_popover_styles")
        s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        s.setSourceCode(
            "document.addEventListener('DOMContentLoaded',function(){"
            "var st=document.createElement('style');"
            "st.textContent='" + css + "';"
            "document.head.appendChild(st);"
            "});"
        )
        return s

    def _on_loaded(self, ok: bool):
        page = self.view.page()
        page.setBackgroundColor(QColor(0, 0, 0, 0))
        if not ok:
            log.warning("聊天页加载失败: %s/chat", self.base_url)
            return
        # 取网页主题背景色，同步弹窗面板颜色（样式已在 DocumentCreation 注入）
        page.runJavaScript(
            "var bg=getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();"
            "bg || 'transparent';",
            self._on_bg_color,
        )
        # 聚焦输入框（等布局稳定）
        QTimer.singleShot(250, lambda: page.runJavaScript(
            "var i=document.getElementById('input'); if(i){i.focus();}"
        ))

    def _on_bg_color(self, value):
        if isinstance(value, str) and value.startswith("#"):
            try:
                self._panel_color = QColor(value)
                self.update()
            except Exception:  # noqa: BLE001
                pass

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        """画圆角面板（颜色与网页主题 --bg 一致）+ 指向悬浮球的箭头。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, W, H, RADIUS, RADIUS)
        painter.fillPath(path, self._panel_color)

        # 箭头（与面板同色，视觉上指向悬浮球）
        tri = QPainterPath()
        cx = W // 2
        if self.direction == "left":     # 气泡在左 → 箭头在左缘
            tri.moveTo(0, H // 2 - 8)
            tri.lineTo(0, H // 2 + 8)
            tri.lineTo(12, H // 2)
        elif self.direction == "right":  # 气泡在右 → 箭头在右缘
            tri.moveTo(W, H // 2 - 8)
            tri.lineTo(W, H // 2 + 8)
            tri.lineTo(W - 12, H // 2)
        elif self.direction == "top":    # 气泡在上 → 箭头在上缘
            tri.moveTo(cx - 8, 0)
            tri.lineTo(cx + 8, 0)
            tri.lineTo(cx, 12)
        else:                            # 气泡在下 → 箭头在下缘
            tri.moveTo(cx - 8, H)
            tri.lineTo(cx + 8, H)
            tri.lineTo(cx, H - 12)
        tri.closeSubpath()
        painter.fillPath(tri, self._panel_color)
        painter.end()
