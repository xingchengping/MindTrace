"""SQLAlchemy ORM 模型：Personal Cognitive OS 全部核心表。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class Event(TimestampMixin, Base):
    """事件：所有采集信号的统一结构化产物。"""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime, index=True)
    app: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    activity: Mapped[str | None] = mapped_column(String(512), nullable=True)
    objects: Mapped[list | None] = mapped_column(JSON, nullable=True)      # 关联对象（文件/URL/函数）
    intent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    project: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5, index=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)  # collector/agent_chat/user


class Task(TimestampMixin, Base):
    """任务状态：Task Intelligence Engine 的核心。"""
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_name: Mapped[str] = mapped_column(String(256), index=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    stage: Mapped[str] = mapped_column(String(32), default="planning", index=True)  # planning/working/blocked/testing/solved/abandoned
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    related_files: Mapped[list | None] = mapped_column(JSON, nullable=True)
    current_problem: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    solutions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    project: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)    # active/closed
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Experience(TimestampMixin, Base):
    """经验记忆：任务完成后的精炼沉淀，七字段。"""
    __tablename__ = "experiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem: Mapped[str] = mapped_column(Text)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    failed_solutions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    final_solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenarios: Mapped[str | None] = mapped_column(Text, nullable=True)
    advice: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5, index=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)      # 用户确认 = 证据分级 A
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/confirmed/draft/discarded
    source_task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ref_count: Mapped[int] = mapped_column(Integer, default=0)           # 被引用次数（重要性 w6）


class GraphNode(TimestampMixin, Base):
    """知识图谱节点：项目/文件/代码/论文/问题/方案/决策/经验。"""
    __tablename__ = "graph_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(512), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    time: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)


class GraphEdge(TimestampMixin, Base):
    """知识图谱边：查看/修改/来源/导致/失败/成功/替代/复用，带时间与置信度。"""
    __tablename__ = "graph_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    src_node_id: Mapped[int] = mapped_column(Integer, ForeignKey("graph_nodes.id"), index=True)
    dst_node_id: Mapped[int] = mapped_column(Integer, ForeignKey("graph_nodes.id"), index=True)
    relation: Mapped[str] = mapped_column(String(32), index=True)
    time: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class MemoryShort(TimestampMixin, Base):
    """短期记忆：原始采集日志，待压缩为事件。"""
    __tablename__ = "memories_short"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime, index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)          # window/file/browser/clipboard/agent_chat
    app: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ChatSession(TimestampMixin, Base):
    """会话上下文：与 Agent 长对话的滚动摘要（不存原文）。"""
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)       # 客户端侧 UUID（Web/悬浮球）
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now, index=True)
    project: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_focus: Mapped[str | None] = mapped_column(Text, nullable=True)   # 当前重点
    current_problem: Mapped[str | None] = mapped_column(Text, nullable=True) # 卡点
    summary_chunks: Mapped[list | None] = mapped_column(JSON, nullable=True) # LSM 压实块（不可变）
    recent_bullets: Mapped[list | None] = mapped_column(JSON, nullable=True) # 活跃条目（有界）
    status: Mapped[str] = mapped_column(String(16), default="working", index=True)
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ChatMessage(TimestampMixin, Base):
    """用户与系统（第二大脑）的对话消息。"""
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[str] = mapped_column(String(64), index=True)       # UUID，SSE 同步键
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), index=True)             # user/assistant
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)        # 引用来源等


class Source(TimestampMixin, Base):
    """来源引用：文件/URL/文档，供答案溯源。"""
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)             # file/url/doc/chat
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Setting(Base):
    """键值设置。"""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserSignal(TimestampMixin, Base):
    """用户反馈信号：驱动重要性评分与提取修正。"""
    __tablename__ = "user_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)             # confirm/rate/dismiss/correct
    target_type: Mapped[str] = mapped_column(String(32), index=True)      # experience/event/reminder/chat
    target_id: Mapped[int] = mapped_column(Integer)
    value: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Trash(TimestampMixin, Base):
    """回收站：被降级删除的记忆，30 天内可恢复。"""
    __tablename__ = "trash"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # 原始记录快照
    deleted_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Notification(TimestampMixin, Base):
    """原生通知与待确认队列：攻克确认/经验候选/实时联想提醒。"""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)             # breakthrough/pending_experience/recall_reminder
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/done/dismissed/expired
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # 按钮动作（保留/仅记录/丢弃/查看）
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Reminder(TimestampMixin, Base):
    """提醒记录：防重复打扰（同主题 24h 内不重复提醒）。"""
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)             # recall/breakthrough/daily_candidate
    subject_key: Mapped[str] = mapped_column(String(256), index=True)     # 去重键（问题指纹）
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ConsolidationLog(TimestampMixin, Base):
    """巩固运行记录（可观测性：最近巩固运行历史）。"""
    __tablename__ = "consolidation_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job: Mapped[str] = mapped_column(String(32), index=True)              # hourly/episodic/daily
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)
