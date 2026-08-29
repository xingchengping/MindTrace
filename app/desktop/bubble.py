"""桌面悬浮球（tkinter 透明置顶窗）—— 果冻小球皮肤（PIL 生成）。

视觉：浅蓝透明渐变 + 多层光泽 + 柔光晕 + 静止轻浮 + 拖动拉伸挤压 + 松手弹性回弹
功能（与之前完全一致）：
  左键单击 → 紧贴悬浮球展开锚定对话小窗（靠左往右/靠右往左/靠上往下/靠下往上），再点关闭
  右键单击 → 功能菜单（打开网页端 / 待确认经验 / 监控目标 / 暂停采集 / 退出）
  角标 → 待确认通知数（每 30s 轮询）
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser

from app.desktop.popover import ChatPopover

log = logging.getLogger("mindtrace.desktop.bubble")

SIZE = 100         # 窗口尺寸
BALL_D = 86        # 果冻球直径（留边给光晕扩散）
HALF = SIZE // 2
TRANSPARENT_BG = "#f0f0f0"  # Canvas 背景色（与 -transparentcolor 配合实现透明）

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk  # noqa: WPS433

    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False


def _lerp_rgba(a: tuple, b: tuple, t: float) -> tuple:
    """RGBA 颜色线性插值。"""
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(4))


class Bubble:
    def __init__(self, base_url: str, on_pause: callable | None = None, on_quit: callable | None = None):
        self.base_url = base_url
        self.on_pause = on_pause
        self.on_quit = on_quit
        self.root = tk.Tk()
        self.root.title("MindTrace")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        # Windows 透明色：Canvas 背景色会被渲染为完全透明
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT_BG)
        except Exception:
            pass
        self.root.geometry(f"{SIZE}x{SIZE}+{120}+{120}")

        self._drag_data = {"x": 0, "y": 0, "moved": False}
        self._last_move = (0.0, 0.0)
        self._snapping = False
        self._idle_running = False
        self._jelly_photo = None
        self._jelly_base = None
        self._float_phase = 0.0

        self.canvas = tk.Canvas(
            self.root, width=SIZE, height=SIZE, highlightthickness=0,
            bg=TRANSPARENT_BG, cursor="hand2"
        )
        self.canvas.pack()
        self._jelly_item = self.canvas.create_image(HALF, HALF, image=None)

        if _HAS_PIL:
            self._jelly_base = self._make_jelly_base()
            self._refresh_jelly(1.0, 1.0)
            self._start_idle_float()
        else:
            self._draw_flat_fallback()

        # 角标（待确认数）
        self.badge = tk.Label(
            self.root, text="", bg="#ff3b30", fg="white", font=("Microsoft YaHei UI", 9, "bold")
        )
        self.badge.place(x=SIZE - 22, y=2)

        self._bind_events()
        self._paused = True   # 默认暂停采集（菜单显示"开始采集"；服务端 start_paused 一致）

        # 待确认角标轮询
        self._poll_badge()

    # ---------- 果冻小球生成（PIL） ----------

    def _make_jelly_base(self) -> Image.Image:
        """生成果冻小球 PNG：通透的浅蓝色玻璃质感。

        参考图特征：
        - 整体是浅蓝色，非常通透
        - 中心略白（光源方向）
        - 边缘渐变到透明
        - 有柔和的光晕
        """
        s = SIZE * 4  # 4x 渲染再降采样，渐变更丝滑
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        cx, cy = s // 2, s // 2
        radius = int(BALL_D / SIZE * s / 2)

        # --- 1. 外部柔光晕（柔和的蓝色光晕） ---
        for i in range(4):
            glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            gr = int(radius * (1.2 + i * 0.12))
            # 光晕颜色：浅蓝色，透明度递减
            alpha = [45, 30, 18, 8][i]
            gd.ellipse([cx - gr, cy - gr, cx + gr, cy + gr],
                       fill=(120, 185, 235, alpha))
            glow = glow.filter(ImageFilter.GaussianBlur(radius // 2 + i * 6))
            img.alpha_composite(glow)

        # --- 2. 主体径向渐变（通透浅蓝色玻璃） ---
        # 高光中心略偏左上
        hotspot_x = int(cx - radius * 0.12)
        hotspot_y = int(cy - radius * 0.15)

        # 颜色停靠点（从中心到边缘）
        # 中心：浅蓝白色（接近参考图的中心）
        # 边缘：渐变到透明
        for r in range(radius, 0, -1):
            t = 1 - (r / radius)  # 0 边缘 → 1 中心

            # 浅蓝色渐变（从边缘透明到中心浅蓝）
            if t < 0.3:
                # 边缘：几乎透明的浅蓝
                col = (140, 195, 240, int(15 + t * 120))
            elif t < 0.6:
                # 中间：半透明浅蓝
                u = (t - 0.3) / 0.3
                col = _lerp_rgba((140, 195, 240, 50), (165, 215, 250, 140), u)
            else:
                # 中心：较亮的浅蓝白
                u = (t - 0.6) / 0.4
                col = _lerp_rgba((165, 215, 250, 140), (210, 235, 255, 200), u)

            # 轻微偏移模拟光源
            ox = int((hotspot_x - cx) * (1 - t) * 0.2)
            oy = int((hotspot_y - cy) * (1 - t) * 0.2)
            d.ellipse([cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy],
                      fill=col, outline=col, width=1)

        # --- 3. 底部内辉光（通透感） ---
        inner = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        id = ImageDraw.Draw(inner)
        ir = int(radius * 0.88)
        id.ellipse([cx - ir, cy - ir + int(radius * 0.1), cx + ir, cy + ir + int(radius * 0.1)],
                   fill=(200, 225, 250, 80))
        inner = inner.filter(ImageFilter.GaussianBlur(radius // 3))
        img.alpha_composite(inner)

        # --- 4. 左上柔和高光（光源方向） ---
        sheen = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sheen)
        # 椭圆形高光
        sr = int(radius * 0.5)
        sx = cx - int(radius * 0.2)
        sy = cy - int(radius * 0.25)
        sd.ellipse([sx - sr, sy - int(sr * 0.7), sx + sr, sy + int(sr * 0.7)],
                   fill=(240, 248, 255, 100))
        sheen = sheen.filter(ImageFilter.GaussianBlur(radius // 4))
        img.alpha_composite(sheen)

        # --- 5. 小圆点高光（点睛之笔） ---
        spot = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        sp = ImageDraw.Draw(spot)
        spot_r = int(radius * 0.15)
        spot_x = cx - int(radius * 0.18)
        spot_y = cy - int(radius * 0.22)
        sp.ellipse([spot_x - spot_r, spot_y - spot_r, spot_x + spot_r, spot_y + spot_r],
                   fill=(255, 255, 255, 140))
        spot = spot.filter(ImageFilter.GaussianBlur(3))
        img.alpha_composite(spot)

        # 降采样到目标尺寸
        return img.resize((SIZE, SIZE), Image.LANCZOS)

    def _refresh_jelly(self, sx: float, sy: float):
        """按缩放因子刷新果冻球（拖动拉伸/回弹时调用）。"""
        if not _HAS_PIL or self._jelly_base is None:
            return
        w = max(int(SIZE * sx), 20)
        h = max(int(SIZE * sy), 20)
        img = self._jelly_base.resize((w, h), Image.BILINEAR)
        self._jelly_photo = ImageTk.PhotoImage(img)
        self.canvas.itemconfig(self._jelly_item, image=self._jelly_photo)

    def _draw_flat_fallback(self):
        """无 PIL 时退回扁平圆球。"""
        self.canvas.delete("all")
        d = BALL_D - 4
        m = (SIZE - d) // 2
        self.canvas.create_oval(m, m, m + d, m + d, fill="#a8d8f0", outline="")
        # 简单高光
        sr = d // 5
        self.canvas.create_oval(m + d // 5, m + d // 6, m + d // 5 + sr, m + d // 6 + sr,
                                fill="white", outline="", stipple="gray50")
        self._jelly_item = None

    # ---------- 动画 ----------

    def _start_idle_float(self):
        """静止时的轻浮动画（HTML: translateY(-12px) scale(1.02), 3s ease-in-out infinite alternate）。"""
        self._idle_running = True
        self._float_phase = 0.0
        self._float_tick()

    def _float_tick(self):
        if not self._idle_running or self._snapping:
            return
        self._float_phase += 0.05
        # 正弦模拟 ease-in-out alternate
        t = math.sin(self._float_phase)
        dy = -6 * t  # 上浮 6px（比 HTML 的 12px 小一些，因为窗口小）
        # 轻微缩放
        scale = 1.0 + 0.01 * t
        if self._jelly_item is not None and _HAS_PIL and self._jelly_base is not None:
            try:
                self.canvas.coords(self._jelly_item, HALF, HALF + dy)
                # 不实际缩放图片（太慢），只移动位置
            except Exception:  # noqa: BLE001
                return
        self.root.after(50, self._float_tick)

    def _stop_idle_float(self):
        self._idle_running = False
        if self._jelly_item is not None:
            try:
                self.canvas.coords(self._jelly_item, HALF, HALF)
            except Exception:  # noqa: BLE001
                pass

    def _snap_back(self, start_sx: float, start_sy: float):
        """松手弹性回弹（模拟 JS 弹性缓出）。"""
        self._snapping = True
        duration = 350
        t0 = time.monotonic()

        def step():
            elapsed = (time.monotonic() - t0) * 1000
            p = min(elapsed / duration, 1.0)
            ease = 1 - (1 - p) ** 3 * math.cos(p * math.pi * 0.3)
            sx = start_sx + (1 - start_sx) * ease
            sy = start_sy + (1 - start_sy) * ease
            self._refresh_jelly(sx, sy)
            if p < 1.0:
                self.root.after(16, step)
            else:
                self._refresh_jelly(1.0, 1.0)
                self._snapping = False
                self._start_idle_float()

        step()

    # ---------- 事件 ----------

    def _bind_events(self):
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right)

    def _on_press(self, event):
        # 拖动前关闭可能打开的对话小窗（位置会过期）
        if getattr(self, "_popover", None) and self._popover.is_open():
            self._popover.close()
            self._popover = None
        self._drag_data = {"x": event.x_root, "y": event.y_root, "moved": False}
        self._last_move = (event.x_root, event.y_root)
        self._stop_idle_float()

    def _on_drag(self, event):
        dx = event.x_root - self._drag_data["x"]
        dy = event.y_root - self._drag_data["y"]
        if abs(dx) > 3 or abs(dy) > 3:
            self._drag_data["moved"] = True
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")
        self._drag_data["x"] = event.x_root
        self._drag_data["y"] = event.y_root

        # 果冻拉伸（完全复刻 HTML JS 逻辑）
        if _HAS_PIL:
            mx = event.x_root - self._last_move[0]
            my = event.y_root - self._last_move[1]
            self._last_move = (event.x_root, event.y_root)
            dist = math.hypot(mx, my)
            speed = min(dist / 8.0, 0.3)
            angle = math.atan2(my, mx)
            stretch = 1 + speed * 0.6
            squash = 1 - speed * 0.3
            sx = 1 + (stretch - 1) * abs(math.cos(angle)) - (1 - squash) * abs(math.sin(angle))
            sy = 1 + (stretch - 1) * abs(math.sin(angle)) - (1 - squash) * abs(math.cos(angle))
            sx = min(max(sx, 0.7), 1.4)
            sy = min(max(sy, 0.7), 1.4)
            self._refresh_jelly(sx, sy)

    def _on_release(self, event):
        if _HAS_PIL and self._drag_data["moved"]:
            self._snap_back(1.0, 1.0)
        if not self._drag_data["moved"]:
            # 单击 → 切换锚定对话小窗（紧贴悬浮球展开）
            self._toggle_popover()
            self._start_idle_float()

    def _toggle_popover(self):
        if getattr(self, "_popover", None) and self._popover.is_open():
            self._popover.close()
            self._popover = None
            return
        try:
            self._popover = ChatPopover(self.root, self.base_url)
        except Exception as exc:  # pragma: no cover
            log.warning("对话小窗打开失败: %s", exc)

    def _on_right(self, event):
        """右键菜单（每次动态构建）：监控目标可选（跟随/锁定窗口列表/关闭）。"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="打开网页端", command=lambda: webbrowser.open(self.base_url))
        menu.add_command(label="待确认经验", command=lambda: webbrowser.open(self.base_url + "/#view-experiences"))

        # 监控目标子菜单（当前项打 ✓）
        sub = tk.Menu(self.root, tearoff=0)
        mode, pattern = self._monitor_state()
        sub.add_command(
            label=("✓ " if mode == "follow" else "") + "跟随前台窗口（默认）",
            command=lambda: self._set_monitor("follow", ""),
        )
        sub.add_separator()
        try:
            from app.collector.window import list_open_windows
            windows = list_open_windows(limit=30)
        except Exception:  # noqa: BLE001
            windows = []
        if mode == "locked" and pattern:
            matched = any(
                (pattern.lower() in (w.get("title", "") or "").lower())
                or ((w.get("title", "") or "").lower() in pattern.lower())
                for w in windows
            )
            if not matched:
                sub.add_command(label="✓ 锁定中：" + pattern[:42])
                sub.add_separator()
        if windows:
            for w in windows:
                label = ((w.get("app", "") + " — ") if w.get("app") else "") + w.get("title", "")
                title = w.get("title", "") or ""
                checked = (
                    mode == "locked" and pattern and (
                        pattern.lower() in title.lower() or title.lower() in pattern.lower()
                    )
                )
                menu_command = (lambda t: lambda: self._set_monitor("locked", t))(title)
                sub.add_command(label=("✓ " if checked else "") + "🔒 " + label[:42], command=menu_command)
        else:
            sub.add_command(label="（暂无可选窗口）", state="disabled")
        menu.add_cascade(label="🎯 监控目标", menu=sub)

        menu.add_separator()
        menu.add_command(label="开始采集" if self._paused else "暂停采集", command=self._toggle_pause)
        menu.add_command(label="免打扰时段…", command=lambda: webbrowser.open(self.base_url + "/#view-settings"))
        menu.add_command(label="设置", command=lambda: webbrowser.open(self.base_url + "/#view-settings"))
        menu.add_separator()
        menu.add_command(label="退出", command=self._quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _monitor_state(self) -> tuple[str, str]:
        """取当前监控模式与 pattern（供菜单对勾；失败兜底 follow）。"""
        try:
            req = urllib.request.Request(self.base_url + "/api/monitor")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("mode", "follow"), (data.get("pattern", "") or "")
        except Exception:  # noqa: BLE001
            return "follow", ""

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
        """翻转暂停状态：开始采集 → on_pause(False)；暂停 → on_pause(True)。"""
        self._paused = not self._paused
        try:
            if self.on_pause:
                self.on_pause(self._paused)
        except Exception:  # noqa: BLE001
            pass

    def _quit(self):
        if self.on_quit:
            self.on_quit()
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    # ---------- 角标 ----------

    def _poll_badge(self):
        def work():
            try:
                req = urllib.request.Request(self.base_url + "/api/notifications/count")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                count = int(data.get("pending", 0))
                self.root.after(0, lambda: self._set_badge(count))
            except Exception:  # noqa: BLE001
                pass
            self.root.after(30000, self._poll_badge)

        threading.Thread(target=work, daemon=True).start()

    def _set_badge(self, count: int):
        try:
            if count > 0:
                self.badge.config(text=str(count) if count < 100 else "99+")
                self.badge.lift()
            else:
                self.badge.config(text="")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 生命周期 ----------

    def run(self):
        try:
            self.root.mainloop()
        except Exception as exc:  # pragma: no cover
            log.warning("悬浮球退出: %s", exc)

    def stop(self):
        try:
            self.root.after(0, self.root.destroy)
        except Exception:  # noqa: BLE001
            pass
