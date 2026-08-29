"""截图视觉理解闭环（Phase 3）：

"截图待理解"事件 → 视觉模型理解成 Event → 立即删除图片。
模型/服务缺失时优雅跳过（Phase 3 可选功能）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from app.core.models import Event

log = logging.getLogger("mindtrace.vision")

_SCREENSHOT_RE = re.compile(r"截图待理解：([^\s（(]+\.(?:png|jpg|jpeg))")


class VisionProcessor:
    def __init__(self, cfg, session_factory, vision, memory):
        self.cfg = cfg
        self.session_factory = session_factory
        self.vision = vision
        self.memory = memory
        self.screenshot_dir = cfg.data_dir / "screenshots"

    @property
    def enabled(self) -> bool:
        return self.vision is not None and self.vision.ready

    def process_pending(self) -> int:
        """处理所有待理解的截图事件。返回处理数。light 档按需懒启动视觉服务。"""
        if self.vision is None:
            return 0
        lazy_started = False
        if not self.vision.ready:
            # 懒启动：有截图待理解才拉起视觉服务，处理完即停（为工作环境留内存）
            if not self.cfg.vis_lazy:
                return 0
            if not (self.cfg.vis_model_path and self.cfg.vis_model_path.exists()
                    and self.cfg.vis_mmproj_path and self.cfg.vis_mmproj_path.exists()):
                return 0
            if not self.vision.start(wait_seconds=self.cfg.vis_server_timeout):
                log.warning("视觉懒启动失败: %s", self.vision.error)
                return 0
            lazy_started = True
        try:
            return self._process()
        finally:
            if lazy_started:
                self.vision.stop()

    def _process(self) -> int:
        db = self.session_factory()
        try:
            rows = (
                db.query(Event)
                .filter(Event.source == "screenshot")
                .order_by(Event.id)
                .limit(20)
                .all()
            )
            if not rows:
                return 0
            count = 0
            for ev in rows:
                m = _SCREENSHOT_RE.search(ev.activity or "")
                if not m:
                    continue
                img = self.screenshot_dir / m.group(1)
                if not img.exists():
                    continue  # 已处理过（图已删）
                try:
                    text = self.vision.vision_chat(
                        img, "描述这张截图的内容，提取关键信息：正在做什么、界面内容、报错信息（如有）。50字以内。"
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("截图理解失败 %s: %s", img.name, exc)
                    continue
                if text:
                    self.memory.remember(
                        f"截图理解：{text[:300]}",
                        source="vision", app="screenshot", importance=0.5,
                        activity=f"截图理解：{text[:300]}",
                    )
                img.unlink(missing_ok=True)  # 理解后立即删除
                count += 1
        finally:
            db.close()
        if count:
            log.info("截图理解完成：%d 张", count)
        return count
