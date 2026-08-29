"""通知分发器：Notification 表 + 系统 Toast（尽力而为）。

任何"需要用户拍板"的事件（攻克确认 / 经验候选 / 实时联想）统一走这里：
1. 写 Notification 行（Web 通知中心 / 悬浮球角标读取）
2. 桌面层启用时尝试系统 Toast
"""
from __future__ import annotations

import logging

from app.core.models import Notification
from app.desktop.toasts import show_toast

log = logging.getLogger("mindtrace.desktop.notify")


class NotifyService:
    def __init__(self, session_factory, toasts_enabled: bool = True,
                 quiet_start: str = "23:00", quiet_end: str = "07:00"):
        self.session_factory = session_factory
        self.toasts_enabled = toasts_enabled
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end

    def _in_quiet_hours(self) -> bool:
        """免打扰时段：不弹系统 Toast（通知仍入库，静默）。"""
        try:
            from datetime import datetime
            now = datetime.now().strftime("%H:%M")
            start, end = self.quiet_start, self.quiet_end
            if start <= end:
                return start <= now < end
            return now >= start or now < end  # 跨天（如 23:00-07:00）
        except Exception:
            return False

    def notify(self, kind: str, title: str, body: str | None = None, action: dict | None = None) -> Notification | None:
        db = self.session_factory()
        try:
            n = Notification(kind=kind, title=title, body=body, status="pending", action=action)
            db.add(n)
            db.commit()
            db.refresh(n)
        except Exception as exc:  # noqa: BLE001
            log.warning("通知入库失败: %s", exc)
            db.rollback()
            return None
        finally:
            db.close()

        if self.toasts_enabled and not self._in_quiet_hours():
            buttons = None
            if kind in ("breakthrough", "pending_experience"):
                buttons = [
                    {"content": "查看", "arguments": "http://127.0.0.1:4000"},
                    {"content": "稍后", "arguments": "http://127.0.0.1:4000"},
                ]
            show_toast(title, body, buttons=buttons)
        return n

    def list_pending(self, limit: int = 50) -> list[dict]:
        db = self.session_factory()
        try:
            rows = (
                db.query(Notification)
                .filter(Notification.status == "pending")
                .order_by(Notification.id.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": n.id,
                    "kind": n.kind,
                    "title": n.title,
                    "body": n.body,
                    "action": n.action,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in rows
            ]
        finally:
            db.close()

    def resolve(self, notif_id: int, status: str) -> bool:
        db = self.session_factory()
        try:
            n = db.get(Notification, notif_id)
            if n is None:
                return False
            n.status = status if status in ("done", "dismissed", "expired") else "done"
            db.commit()
            return True
        finally:
            db.close()
