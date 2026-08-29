"""系统托盘（pystray）。失败时静默降级（悬浮球已覆盖主要入口）。"""
from __future__ import annotations

import logging
import threading
import webbrowser
from io import BytesIO

log = logging.getLogger("mindtrace.desktop.tray")


def _make_icon():
    try:
        from PIL import Image, ImageDraw
    except Exception:  # pragma: no cover
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(10, 132, 255, 255))
    d.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 255))
    return img


def run_tray(base_url: str, on_quit=None) -> bool:
    """在后台线程运行托盘。返回是否启动成功。"""
    try:
        import pystray
    except Exception:  # pragma: no cover
        return False
    icon_img = _make_icon()
    if icon_img is None:
        return False

    def open_web(icon=None, item=None):
        webbrowser.open(base_url)

    def quit_app(icon=None, item=None):
        icon.stop()
        if on_quit:
            on_quit()

    try:
        icon = pystray.Icon(
            "mindtrace",
            icon_img,
            "MindTrace · Personal Cognitive OS",
            menu=pystray.Menu(
                pystray.MenuItem("打开界面", open_web),
                pystray.MenuItem("退出", quit_app),
            ),
        )
        threading.Thread(target=icon.run, daemon=True).start()
        return True
    except Exception as exc:  # pragma: no cover
        log.warning("托盘启动失败: %s", exc)
        return False
