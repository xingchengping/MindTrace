"""锚定悬浮球的对话小窗（Tkinter 无边框 Toplevel）。

紧贴悬浮球展开：
  靠左 → 向右展开；靠右 → 向左展开；靠上 → 向下展开；靠下 → 向上展开；居中 → 任选（空间最大侧）
带指向悬浮球的小箭头；含：消息区 + 输入框 + 发送；SSE 流式；与网页端共享同一会话。
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import tkinter as tk
import urllib.request

log = logging.getLogger("mindtrace.desktop.popover")

W, H = 380, 440
GAP = 10
BG = "#1e1e20"          # 深色毛玻璃观感（Tkinter 无法真毛玻璃，用半透明深色）
BG2 = "#2a2a2e"
FG = "#f5f5f7"
FG_SUB = "#a1a1a6"
ACCENT = "#0a84ff"
USER_BG = "#0a84ff"
FONT = ("Microsoft YaHei UI", 11)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")


def _screen_work_area(anchor: tk.Tk, cx: int, cy: int) -> tuple[int, int, int, int]:
    """取悬浮球所在显示器的可用区域（多显示器正确）。"""
    try:
        import win32api
        hmon = win32api.MonitorFromPoint((cx, cy), 0)  # MONITOR_DEFAULTTONEAREST
        info = win32api.GetMonitorInfo(hmon)
        return tuple(info["Work"])
    except Exception:  # pragma: no cover
        return (0, 0, anchor.winfo_screenwidth(), anchor.winfo_screenheight())


def _anchor_position(anchor: tk.Tk) -> tuple[int, int, int, int, tuple[int, int, int, int]]:
    """展开方向：默认向右；仅当气泡贴近屏幕右缘（右边放不下弹窗）才向左。

    垂直方向只做居中对齐与屏幕内钳制，不参与方向选择。
    """
    bx = anchor.winfo_rootx()
    by = anchor.winfo_rooty()
    bw = anchor.winfo_width() or 120
    bh = anchor.winfo_height() or 120
    work = _screen_work_area(anchor, bx + bw // 2, by + bh // 2)
    wl, wt, wr, wb = work

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


class ChatPopover:
    """紧贴悬浮球的迷你对话窗。"""

    def __init__(self, anchor: tk.Tk, base_url: str):
        self.anchor = anchor
        self.base_url = base_url.rstrip("/")
        self.session_id: str | None = None
        self._queue: queue.Queue = queue.Queue()
        self._streaming = False

        self.win = tk.Toplevel(anchor)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=BG)
        try:
            self.win.attributes("-alpha", 0.97)
        except Exception:
            pass

        x, y, direction = _anchor_position(anchor)
        self.win.geometry(f"{W}x{H}+{x}+{y}")

        self._build_ui(direction)
        self._poll_queue()
        threading.Thread(target=self._init_session, daemon=True).start()

    # ---------- UI ----------

    def _build_ui(self, direction: str):
        container = tk.Frame(self.win, bg=BG)
        container.pack(fill="both", expand=True)

        # 顶部小标题 + 关闭（悬浮球在上方时，箭头内嵌在标题条上缘）
        head = tk.Frame(container, bg=BG)
        head.pack(side="top", fill="x")
        if direction == "top":
            self._notch_strip(head, "top")
        tk.Label(head, text="◉ MindTrace", bg=BG, fg=FG, font=FONT_TITLE).pack(side="left", padx=12, pady=8)
        tk.Button(head, text="✕", bg=BG, fg=FG_SUB, font=FONT_SMALL, relief="flat", bd=0,
                  activebackground=BG2, activeforeground=FG, command=self.close).pack(side="right", padx=8)

        # 输入区（先占底部，避免被展开区挤没；悬浮球在下方时箭头内嵌在输入栏下缘）
        foot = tk.Frame(container, bg=BG)
        foot.pack(side="bottom", fill="x", padx=10, pady=(4, 4))
        self.entry = tk.Entry(foot, bg="#333338", fg=FG, insertbackground=FG, font=FONT,
                              relief="flat", bd=0, highlightthickness=1,
                              highlightbackground="#4a4a50", highlightcolor=ACCENT)
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self.entry.bind("<Return>", lambda e: self.send())
        self.send_btn = tk.Button(foot, text="发送", bg=ACCENT, fg="white", font=FONT_SMALL,
                                  relief="flat", bd=0, activebackground="#0a6fd6",
                                  activeforeground="white", padx=14, command=self.send)
        self.send_btn.pack(side="right")
        if direction == "bottom":
            self._notch_strip(foot, "bottom")

        # 消息区（最后 pack，填充中间剩余空间）
        self.text = tk.Text(container, bg=BG2, fg=FG, font=FONT, wrap="word",
                            relief="flat", bd=0, padx=12, pady=10, highlightthickness=0,
                            state="disabled", spacing1=2, spacing3=6)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("user", foreground="#ffffff", background=USER_BG,
                                lmargin1=60, lmargin2=60, rmargin=8, spacing1=4, spacing3=4)
        self.text.tag_configure("assistant", foreground=FG, background=BG2,
                                lmargin1=8, lmargin2=8, rmargin=60, spacing1=4, spacing3=4)
        self.text.tag_configure("sys", foreground=FG_SUB, font=FONT_SMALL)

        # 水平展开：悬浮箭头（中段，不与输入区重叠）
        if direction in ("left", "right"):
            self._draw_side_notch(direction)

        self.entry.focus_set()

    def _notch_strip(self, frame: tk.Frame, where: str):
        """把箭头内嵌进标题条/输入栏的边缘，不遮挡任何控件。"""
        notch = tk.Canvas(frame, height=12, bg=BG, highlightthickness=0)
        cx = W // 2
        if where == "top":
            notch.pack(side="top", fill="x")
            notch.create_polygon(cx - 7, 12, cx + 7, 12, cx, 0, fill=BG, outline="")
        else:
            notch.pack(side="bottom", fill="x")
            notch.create_polygon(cx - 7, 0, cx + 7, 0, cx, 12, fill=BG, outline="")

    def _draw_side_notch(self, direction: str):
        """水平展开时在左/右边缘画箭头（垂直居中，不与输入区重叠）。"""
        notch = tk.Canvas(self.win, width=14, height=14, bg=BG, highlightthickness=0)
        if direction == "left":
            notch.place(x=-1, y=H // 2 - 7)
            notch.create_polygon(14, 0, 14, 14, 0, 7, fill=BG, outline="")
        else:
            notch.place(x=W - 13, y=H // 2 - 7)
            notch.create_polygon(0, 0, 0, 14, 14, 7, fill=BG, outline="")

    # ---------- 会话 ----------

    def _safe_after(self, ms: int, fn):
        """线程安全地调度 UI 更新；mainloop 未运行时静默跳过。"""
        try:
            self.win.after(ms, fn)
        except Exception:  # noqa: BLE001
            pass

    def _init_session(self):
        try:
            req = urllib.request.Request(self.base_url + "/api/chat/sessions")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            sessions = data.get("sessions") or []
            if sessions:
                self.session_id = sessions[0]["client_id"]  # 最近会话，与网页端同步
                self._load_messages()
                return
        except Exception as exc:  # noqa: BLE001
            log.debug("读取会话失败: %s", exc)
        try:
            req = urllib.request.Request(self.base_url + "/api/chat/sessions", method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.session_id = data["client_id"]
        except Exception as exc:  # noqa: BLE001
            log.warning("新建会话失败: %s", exc)
            self._append("sys", "无法连接 MindTrace 服务")
        self._safe_after(0, lambda: self.entry.focus_set())

    def _load_messages(self):
        try:
            req = urllib.request.Request(
                self.base_url + "/api/chat/sessions/" + self.session_id + "/messages"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for m in (data.get("messages") or []):
                self._append(m["role"], m["content"])
        except Exception as exc:  # noqa: BLE001
            log.debug("历史加载失败: %s", exc)

    # ---------- 发送与流式 ----------

    def send(self):
        text = self.entry.get().strip()
        if not text or self._streaming:
            return
        self.entry.delete(0, "end")
        self._append("user", text)
        self._streaming = True
        self.send_btn.config(state="disabled")
        threading.Thread(target=self._stream, args=(text,), daemon=True).start()

    def _stream(self, text: str):
        body = json.dumps({"message": text, "session_id": self.session_id}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        full = ""
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                buf = ""
                for raw in resp:
                    buf += raw.decode("utf-8", "replace")
                    while "\n\n" in buf:
                        part, buf = buf.split("\n\n", 1)
                        data_line = None
                        for line in part.split("\n"):
                            if line.startswith("data:"):
                                data_line = line[5:].strip()
                        if not data_line:
                            continue
                        try:
                            payload = json.loads(data_line)
                        except json.JSONDecodeError:
                            continue
                        delta = payload.get("delta")
                        if delta:
                            full += delta
                            self._queue.put(("delta", delta))
            self._queue.put(("done", None))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("error", str(exc)))
        finally:
            self._streaming = False
            self._safe_after(0, lambda: self.send_btn.config(state="normal"))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "delta":
                    self._append_stream(payload)
                elif kind == "done":
                    self._append("sys", "")
                elif kind == "error":
                    self._append("sys", f"[连接错误] {payload}")
        except queue.Empty:
            pass
        self._safe_after(60, self._poll_queue)

    # ---------- 文本渲染 ----------

    def _append(self, role: str, text: str):
        def do():
            self.text.config(state="normal")
            self.text.insert("end", text + "\n", role)
            self.text.config(state="disabled")
            self.text.see("end")
        self._safe_after(0, do)

    def _append_stream(self, delta: str):
        def do():
            self.text.config(state="normal")
            self.text.insert("end", delta, "assistant")
            self.text.config(state="disabled")
            self.text.see("end")
        self._safe_after(0, do)

    # ---------- 生命周期 ----------

    def close(self):
        try:
            self.win.destroy()
        except Exception:  # noqa: BLE001
            pass

    def is_open(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False
