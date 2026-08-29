"""全屏边缘流光 + 同步唤醒音效（Apple Intelligence / Siri 唤醒风格）。

视觉（纯 QPainter，回到"边框渐变线条"方案）：
  - 屏幕四边各画一条彩色渐变光带：每段沿边单一色相、步长小（无缝彩虹），
    整圈色相随时间缓慢旋转流动；
  - 多层光晕（主带 + 外扩层）宽度递增、透明度递减 → 柔和漫射；
  - 每段从屏幕边缘向屏幕内线性渐隐到透明（无硬边、无画框感）；
  - 屏幕中央完全透明，不遮挡任何内容。
  - 所有 hue 均归一化到 [0,1)，杜绝 QColor::fromHsvF 越界警告。

音效（动态生成，无资源文件）：
  - 代码合成短促清亮的唤醒 Chime（A5/E6/A6 泛音 + 指数衰减包络 + 敲击瞬态），
    经 winsound SND_MEMORY|SND_ASYNC 非阻塞播放，与淡入同步、无延迟。

窗口样式（等价 Win32 要求）：
  - 鼠标穿透          → Qt.WindowTransparentForInput（WS_EX_TRANSPARENT）
  - 分层透明          → WA_TranslucentBackground（WS_EX_LAYERED）
  - 置顶              → WindowStaysOnTopHint
  - 无任务栏图标      → Qt.Tool（WS_EX_TOOLWINDOW）

控制：
  - show_glow(): 播放 Chime + 淡入 + 启动动画（默认数秒后自动淡出）
  - hide_glow(): 平滑淡出隐藏（幂等）
  - set_params(): 发光宽度 / 羽化 / 速度 / 透明度 动态调整
"""
from __future__ import annotations

import array
import io
import logging
import math
import time
import wave

# 必须在 PySide6 导入前：统一环境配置（沙箱/软件渲染/便携缓存）+ 预载 ICU
from app.desktop.qt_compat import preload_qt  # noqa: E402,F401

preload_qt()

from PySide6.QtCore import QPropertyAnimation, QRectF, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QRadialGradient  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from app.core.paths import EXE_DIR, RESOURCE_DIR  # noqa: E402

log = logging.getLogger("mindtrace.desktop.glow")


def _find_media(name: str):
    """唤醒音效素材定位：exe 旁（便携可替换）→ 捆绑目录（_internal）→ 项目根（dev）。"""
    for base in (EXE_DIR, RESOURCE_DIR):
        p = base / name
        if p.exists():
            return p
    return None


# 唤醒音效素材（用户提供）：exe 旁 / 捆绑目录 / 项目根
_MUSIC_MP3 = _find_media("music.mp3")
_MUSIC_WAV = _find_media("music.wav")

# 默认参数（可经 set_params 动态调整）
DEFAULT_BORDER = 50       # 发光宽度（px，向屏幕内部延伸）
DEFAULT_BLUR = 30         # 羽化程度（0=较锐，越大光晕越扩散柔和）
DEFAULT_SPEED = 60        # 整圈色相旋转速度（度/秒，柔和流动）
DEFAULT_OPACITY = 0.9     # 边缘最亮处不透明度
AUTO_HIDE_MS = 8000       # 显示后自动淡出时长（Siri 风格短暂唤醒）

SEGMENTS = 20             # 每条边分段的渐变段数（步长 360/(4*seg) ≈ 4.5°，仍无缝）
LAYERS = 3                # 光晕层数（主带 + 2 层外扩）


def _norm_hue(hue: float) -> float:
    """归一化 hue 到 [0,1)，防止 QColor::fromHsvF 越界警告。"""
    return ((hue % 1.0) + 1.0) % 1.0


# ---------------------------------------------------------------------------
# 唤醒音效：动态合成短促清亮的 Chime（A5/E6/A6 泛音 + 敲击瞬态）
# ---------------------------------------------------------------------------

_chime_cache: bytes | None = None


def _build_chime_wav(sample_rate: int = 44100, duration: float = 0.5) -> bytes:
    """合成 → wav 字节（单声道 16bit）。A5(880) 为主，叠加 E6/A6 泛音 + 短促敲击瞬态。"""
    n = int(sample_rate * duration)
    buf = array.array("h")
    partials = (
        (880.00, 1.00),    # A5
        (1318.51, 0.45),   # E6
        (1760.00, 0.22),   # A6
        (2637.02, 0.10),   # E7（泛音，加"清亮"感）
    )
    for i in range(n):
        t = i / sample_rate
        env = math.exp(-t * 8.5) * (1.0 - math.exp(-t * 900.0))
        v = 0.0
        for f, amp in partials:
            p_env = math.exp(-t * (4.0 + f / 300.0))
            v += amp * p_env * math.sin(2.0 * math.pi * f * t)
        if t < 0.008:
            v += 0.35 * (1.0 - t / 0.008) * math.sin(2.0 * math.pi * 2400.0 * t)
        v *= env * 0.55
        v = max(-1.0, min(1.0, v))
        buf.append(int(v * 32767))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(buf.tobytes())
    return out.getvalue()


def _mci_play(mp3: str, alias: str = "mt_wake") -> bool:
    """Windows MCI 异步播放 mp3（零依赖）。返回是否成功打开。"""
    try:
        import ctypes
        winmm = ctypes.windll.winmm
        # 先关旧实例（避免 alias 冲突）
        try:
            winmm.mciSendStringW(f"close {alias}", None, 0, None)
        except Exception:  # noqa: BLE001
            pass
        # 打开（MP3 → mpegvideo 设备）
        err = winmm.mciSendStringW(
            f'open "{mp3}" type mpegvideo alias {alias}', None, 0, None)
        if err != 0:   # 无 mpegvideo 设备时退化为自动类型
            err = winmm.mciSendStringW(f'open "{mp3}" alias {alias}', None, 0, None)
        if err != 0:
            return False
        winmm.mciSendStringW(f"play {alias}", None, 0, None)
        # 延迟关闭（播放完后释放 alias），不阻塞主线程
        import threading
        threading.Timer(3.0, _mci_close, args=(alias,)).start()
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("MCI 播放失败: %s", exc)
        return False


def _mci_close(alias: str):
    try:
        import ctypes
        ctypes.windll.winmm.mciSendStringW(f"close {alias}", None, 0, None)
    except Exception:  # noqa: BLE001
        pass


_ffplay_proc = None   # 当前播放进程（防重入）


def _ffplay_play(mp3: str) -> bool:
    """ffplay 子进程播放 mp3（自带解码+音频输出，异步；重复触发先停旧的）。"""
    global _ffplay_proc
    try:
        import shutil
        import subprocess
        ffplay = shutil.which("ffplay")
        if not ffplay:
            return False
        # 停掉上一个播放（避免叠加）
        if _ffplay_proc is not None:
            try:
                _ffplay_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        _ffplay_proc = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(mp3)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("ffplay 播放失败: %s", exc)
        return False


def _is_valid_pcm_wav(path) -> bool:
    """快速校验文件是真正的 PCM WAV（winsound 只支持 PCM；把 mp3 改名成 .wav
    会静默失败，导致"明明有文件却没声音"）。"""
    try:
        import wave
        with wave.open(str(path), "rb") as w:
            return w.getsampwidth() in (1, 2)  # 8/16-bit PCM
    except Exception:  # noqa: BLE001
        return False


def play_chime() -> None:
    """播放唤醒音效：MCI(mp3) → ffplay(mp3) → MCI(wav) → ffplay(wav) → winsound(wav) → Chime。

    - mp3/wav 都先走 MCI（mp3=mpegvideo、wav=waveaudio 设备，任何 Windows 都有）；
    - winsound 异步播放在本机实测会 "Cannot play asynchronously from memory"，
      只作最后兜底，不能依赖它。
    """
    global _chime_cache
    # 1) mp3：MCI → ffplay
    if _MUSIC_MP3 is not None:
        if _mci_play(str(_MUSIC_MP3), alias="mt_wake") or _ffplay_play(str(_MUSIC_MP3)):
            return
    # 2) wav：MCI（waveaudio 设备，最稳）→ ffplay
    if _MUSIC_WAV is not None:
        if _mci_play(str(_MUSIC_WAV), alias="mt_wake_wav") or _ffplay_play(str(_MUSIC_WAV)):
            return
    # 3) wav → winsound（仅当文件是真正的 PCM WAV；失败也不报错，返回 None 无法判断）
    if _MUSIC_WAV is not None and _is_valid_pcm_wav(_MUSIC_WAV):
        try:
            import winsound
            winsound.PlaySound(
                str(_MUSIC_WAV),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("winsound 播放 music.wav 失败: %s", exc)
    # 4) 合成 Chime（最后兜底）
    try:
        import winsound
        if _chime_cache is None:
            _chime_cache = _build_chime_wav()
        winsound.PlaySound(
            _chime_cache,
            winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception as exc:  # noqa: BLE001  # 无声卡/受限环境静默
        log.debug("唤醒音效不可用: %s", exc)


# ---------------------------------------------------------------------------
# 流光覆盖层
# ---------------------------------------------------------------------------


class GlowOverlay(QWidget):
    """全屏边缘流光（QPainter 边框渐变线条 + 多层光晕）。"""

    def __init__(self):
        super().__init__()
        self._border = DEFAULT_BORDER
        self._blur = DEFAULT_BLUR
        self._speed = DEFAULT_SPEED
        self._opacity = DEFAULT_OPACITY
        self._t0 = time.monotonic()
        self._fade: QPropertyAnimation | None = None
        self._auto_timer: QTimer | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool                       # 无任务栏图标（WS_EX_TOOLWINDOW）
            | Qt.WindowType.WindowTransparentForInput  # 鼠标穿透（WS_EX_TRANSPARENT）
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # WS_EX_LAYERED
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.setWindowOpacity(0.0)

        # 60fps 动画帧
        self._ticker = QTimer(self)
        self._ticker.setInterval(16)
        self._ticker.timeout.connect(self._on_tick)

    # ---------- 绘制 ----------

    def _on_tick(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return

        t = time.monotonic() - self._t0
        hue0 = t * self._speed          # 整圈色相缓慢旋转（流动）
        op = self._opacity
        B = max(20, int(self._border))

        # 四边主体（不含角落）+ 四角径向补角 → 完全无缝、无缝隙
        self._draw_edges(painter, w, h, hue0, B, op)

        painter.end()

    def _draw_edges(self, p: QPainter, w: int, h: int, hue0: float,
                    width: int, alpha: float):
        """四边铺满（无缝）：四条边主体（避开角落）+ 四角 QRadialGradient 补角。

        边主体：顶/底边 x∈[B, w-B]、左/右边 y∈[B, h-B]，色相沿边连续环绕；
        四角：径向渐变（半径 1.6B，中心亮边缘淡）覆盖角部，与边重叠处由角主导
        → 交界无 0 值、无缝隙。
        """
        B = float(width)
        # ---- 四边主体 ----
        self._fill_edge(p, QRectF(B, 0.0, float(w) - 2 * B, B), "y", hue0, 0.00, 0.25, alpha)    # 顶
        self._fill_edge(p, QRectF(B, float(h) - B, float(w) - 2 * B, B), "y", hue0, 0.50, 0.75, alpha)  # 底
        self._fill_edge(p, QRectF(0.0, B, B, float(h) - 2 * B), "x", hue0, 0.75, 1.00, alpha)   # 左
        self._fill_edge(p, QRectF(float(w) - B, B, B, float(h) - 2 * B), "x", hue0, 0.25, 0.50, alpha)  # 右
        # ---- 四角补角（径向渐变；角位 0/1/2/3 对应暖锚点段） ----
        self._fill_corner(p, 0.0, 0.0, B, 0, hue0, alpha)            # 左上：warm[0]→warm[1]
        self._fill_corner(p, float(w), 0.0, B, 1, hue0, alpha)       # 右上：warm[1]→warm[2]
        self._fill_corner(p, float(w), float(h), B, 2, hue0, alpha)  # 右下：warm[2]→warm[3]
        self._fill_corner(p, 0.0, float(h), B, 3, hue0, alpha)       # 左下：warm[3]→warm[4]=warm[0]

    def _fill_edge(self, p: QPainter, rect: QRectF, normal: str, hue0: float,
                   h_a: float, h_b: float, alpha: float):
        """单条边主体：暖色正弦波浪（色相沿边流动+波动）+ DestinationIn 向屏幕内羽化。

        色相固定在橙→金→黄暖段（0.05-0.21，无洋红无冷色），沿边做 2 个正弦波，
        波浪相位随时间连续推移 → 色彩流动 + 波动，无取模跳变。
        """
        x0, y0, x1, y1 = rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height()
        if normal == "y":            # 顶/底边：色相沿 x，向内羽化沿 y
            grad = QLinearGradient(x0, y0, x1, y0)
            fade = QLinearGradient(0, y0, 0, y1) if y0 == 0 else QLinearGradient(0, y1, 0, y0)
        else:                        # 左/右边：色相沿 y，向内羽化沿 x
            grad = QLinearGradient(x0, y0, x0, y1)
            fade = QLinearGradient(x0, 0, x1, 0) if x0 == 0 else QLinearGradient(x1, 0, x0, 0)

        # 暖色波浪：相位随时间连续增长（hue0 度 → 秒），不取模 → 平滑流动
        phase = hue0 / 60.0                      # ≈ t（秒）
        stops = 16
        a0 = int(max(0.0, min(1.0, alpha)) * 255)
        for k in range(stops + 1):
            u = k / stops
            # 沿边位置（0-1，整条边 2 个波浪周期）
            along = u
            # 色相在暖段 0.06-0.18（22°-65° 橙→金→黄）内正弦摆动：中心 0.12、幅度 0.06
            hue = 0.12 + 0.06 * math.sin(2.0 * math.pi * (along * 2.0) + phase * 1.6)
            hue = _norm_hue(hue)
            # 亮度同步微起伏（波动感）
            val = 0.78 + 0.06 * math.sin(2.0 * math.pi * (along * 2.0) + phase * 1.6 + 1.3)
            col = QColor.fromHsvF(hue, 0.55, val)
            grad.setColorAt(u, QColor(col.red(), col.green(), col.blue(), 255))

        # 向内渐变：边缘保持 grad 满 alpha（fade=255 → 不衰减），向屏幕内平滑到 0。
        # 用 smoothstep 连续曲线保证单调平滑（无突变）。
        for k in range(9):
            u = k / 8.0
            s = u * u * (3.0 - 2.0 * u)          # 0→1 平滑
            fade.setColorAt(u, QColor(255, 255, 255, int(255 * (1.0 - s))))

        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.fillRect(rect, grad)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        p.fillRect(rect, fade)
        p.restore()

    def _fill_corner(self, p: QPainter, cx: float, cy: float, border: float,
                     corner_seg: int, hue0: float, alpha: float):
        """四角径向补角：中心在角点，半径 1.6B，色彩由角点向内渐隐到透明。

        corner_seg: 角所在暖锚点段（0/1/2/3），色相取段中点，与相邻边衔接。
        """
        r = QRadialGradient(cx, cy, border * 1.6, cx, cy)
        # 与边一致的暖色波浪：角在"所在边的起点"相位（corner_seg 0/1/2/3 对应周长 0/0.25/0.5/0.75）
        phase = hue0 / 60.0
        # 角所在周长位置对应的波浪位置：corner_seg/4（每条边沿=1/4 周长 → 波浪沿 = corner_seg*0.5）
        along = corner_seg * 0.5
        hue_n = _norm_hue(0.12 + 0.06 * math.sin(2.0 * math.pi * (along * 2.0) + phase * 1.6))
        val = 0.78 + 0.06 * math.sin(2.0 * math.pi * (along * 2.0) + phase * 1.6 + 1.3)
        col = QColor.fromHsvF(hue_n, 0.55, val)
        a0 = int(max(0.0, min(1.0, alpha)) * 255)
        r.setColorAt(0.0, QColor(col.red(), col.green(), col.blue(), a0))
        r.setColorAt(0.5, QColor(col.red(), col.green(), col.blue(), int(a0 * 0.7)))
        r.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
        p.fillRect(QRectF(cx - border * 1.6, cy - border * 1.6,
                          border * 3.2, border * 3.2), r)

    # ---------- 公开控制 API ----------

    def set_params(self, border: float | None = None, blur: float | None = None,
                   speed: float | None = None, opacity: float | None = None) -> None:
        """动态调整：发光宽度 / 羽化漫射 / 流动速度 / 透明度（即时生效）。"""
        if border is not None:
            self._border = max(10, int(border))
        if blur is not None:
            self._blur = max(0, int(blur))
        if speed is not None:
            self._speed = max(0, float(speed))
        if opacity is not None:
            self._opacity = min(1.0, max(0.0, float(opacity)))
        self.update()

    def show_glow(self, auto_hide_ms: int = AUTO_HIDE_MS) -> None:
        """播放唤醒 Chime + 显示 + 淡入 + 启动动画；默认 auto_hide_ms 后自动淡出。"""
        self._t0 = time.monotonic()
        play_chime()                      # 音画同步：淡入瞬间播放
        self.show()
        self.raise_()
        self._ticker.start()
        self.update()

        self._stop_fade()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(450)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(1.0)
        fade.start()
        self._fade = fade

        if auto_hide_ms and auto_hide_ms > 0:
            self._stop_auto_timer()
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self.hide_glow)
            timer.start(auto_hide_ms)
            self._auto_timer = timer

    def hide_glow(self) -> None:
        """平滑淡出并隐藏（幂等）。"""
        self._ticker.stop()
        self._stop_auto_timer()
        self._stop_fade()

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(350)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)
        fade.finished.connect(lambda: (self.hide(), self.update()))
        fade.start()
        self._fade = fade

    # ---------- 内部 ----------

    def _stop_fade(self):
        if self._fade is not None:
            try:
                self._fade.stop()
            except Exception:  # pragma: no cover
                pass
            self._fade = None

    def _stop_auto_timer(self):
        if self._auto_timer is not None:
            try:
                self._auto_timer.stop()
            except Exception:  # pragma: no cover
                pass
            self._auto_timer = None
