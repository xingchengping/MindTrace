"""文件事件采集（watchdog）。

监听用户配置的工作目录，文件创建/修改/删除 → memories_short（source=file）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from threading import Thread

log = logging.getLogger("mindtrace.collector.files")

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    _HAS_WATCHDOG = True
except Exception:  # pragma: no cover
    _HAS_WATCHDOG = False


class _Handler(FileSystemEventHandler):
    def __init__(self, on_event):
        self._on_event = on_event

    def _emit(self, event, kind: str):
        if event.is_directory:
            return
        try:
            path = Path(event.src_path)
        except Exception:
            return
        # 过滤缓存/隐藏/临时
        low = path.name.lower()
        if low.startswith(".") or low.endswith((".tmp", ".pyc", "~", ".swp")) or "__pycache__" in str(path):
            return
        if ".git" in path.parts:
            return
        self._on_event(
            {
                "source": "file",
                "kind": kind,
                "path": str(path),
                "name": path.name,
                "ext": path.suffix.lower(),
                "dir": str(path.parent),
                "time": datetime.now(),
            }
        )

    def on_created(self, event):
        self._emit(event, "created")

    def on_modified(self, event):
        self._emit(event, "modified")

    def on_deleted(self, event):
        self._emit(event, "deleted")


class FileWatcher:
    def __init__(self, dirs: list[str], on_event):
        self.dirs = [str(Path(d).resolve()) for d in dirs if d]
        self._on_event = on_event
        self._observer: Observer | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if not _HAS_WATCHDOG:
            log.warning("watchdog 不可用，文件采集已停用")
            return
        if not self.dirs:
            return
        handler = _Handler(self._on_event)
        self._observer = Observer()
        for d in self.dirs:
            try:
                self._observer.schedule(handler, d, recursive=True)
                log.info("监听目录: %s", d)
            except Exception as exc:  # pragma: no cover
                log.warning("监听 %s 失败: %s", d, exc)
        self._observer.start()

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
