"""Agent 会话上下文：LSM 式"追加 + 定期压实"。

结构 = summary_chunks[]（压缩块，写入即冻结） + recent_bullets[]（活跃条目，有界）。
只追加事实，从不重写全文——漂移只影响最新窗口，历史永远稳定。
"""
from __future__ import annotations

import logging
from typing import Callable

from app.core.models import ChatSession

log = logging.getLogger("mindtrace.tasks.session")


def agent_client_id(app: str, project: str | None) -> str:
    return f"agent:{app}:{project or 'default'}"


def get_or_create_agent_session(db, app: str, project: str | None) -> ChatSession:
    cid = agent_client_id(app, project)
    s = db.query(ChatSession).filter(ChatSession.client_id == cid).first()
    if s is None:
        s = ChatSession(
            client_id=cid,
            title=f"Agent会话：{app}" + (f" / {project}" if project else ""),
            project=project,
            status="working",
            summary_chunks=[],
            recent_bullets=[],
        )
        db.add(s)
        db.flush()
    return s


def add_bullets(
    db,
    session: ChatSession,
    new_bullets: list[str],
    cap: int = 200,
    compact_batch: int = 50,
    compact_fn: Callable[[list[str]], str] | None = None,
) -> None:
    """追加事实条目；超上限时把最旧一批压实为摘要块（冻结）。"""
    bullets = list(session.recent_bullets or [])
    chunks = list(session.summary_chunks or [])
    bullets.extend(new_bullets)
    # 简单去重（连续重复/近似）
    deduped: list[str] = []
    seen: set[str] = set()
    for b in bullets:
        key = b.strip()[:40]
        if key and key not in seen:
            seen.add(key)
            deduped.append(b)
    bullets = deduped

    while len(bullets) > cap and compact_fn is not None:
        old, bullets = bullets[:compact_batch], bullets[compact_batch:]
        try:
            summary = compact_fn(old)
        except Exception as exc:  # noqa: BLE001
            log.warning("压实失败: %s", exc)
            break
        if summary:
            chunks.append(summary)
        # 防止无限循环：若压实后仍超限，强制截断最旧
        if len(bullets) > cap:
            bullets = bullets[-(cap // 2):]

    session.recent_bullets = bullets[-cap:]
    session.summary_chunks = chunks[-20:]
    db.add(session)


def compact_via_llm(llm, old_bullets: list[str]) -> str:
    """用 LLM 把一批旧条目压实为一段摘要。"""
    if not old_bullets:
        return ""
    lines = "\n".join(f"- {b}" for b in old_bullets)
    prompt = (
        "把下面这段工作过程记录压缩成一段 80 字以内的中文摘要，"
        "保留关键动作、卡点和结果。只输出摘要。\n\n" + lines
    )
    try:
        text = llm.chat(
            [{"role": "system", "content": "你是精炼的记录摘要器。"},
             {"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return text.strip()[:200]
    except Exception:  # noqa: BLE001
        return ""
