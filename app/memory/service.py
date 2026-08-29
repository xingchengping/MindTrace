"""记忆服务：事件入库（含向量化）、HyDE+重排检索、上下文组装。

Phase 1：对话问答自动沉淀为事件；"记住：xxx" 手动入库；
回忆/经验类问题走向量检索 + 来源引用。
Phase 2（尖端检索）：HyDE（LLM 生成假设文档扩充查询）+ 多信号融合重排
（向量 + 关键词重叠 + 时效衰减 + 重要度），纯本地零新增模型内存。
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import Callable

from sqlalchemy.orm import sessionmaker

from app.core.models import Event, Experience, Task
from app.llm.engine import LlamaServer
from app.memory.vectors import VectorStore

log = logging.getLogger("mindtrace.memory")

ENTITY_EVENT = "event"
ENTITY_EXPERIENCE = "experience"
ENTITY_TASK = "task"

# 融合重排权重：向量 45% + 关键词 25% + 时效 15% + 重要度 15%
W_VEC, W_KW, W_REC, W_IMP = 0.45, 0.25, 0.15, 0.15
RECENCY_HALF_DAYS = 60.0   # 时效半衰期（天）：60 天后重要性折半


def _keyword_overlap(query: str, text: str) -> float:
    """关键词重叠分：查询字符/单词出现在文本中的比例（中英文通用）。"""
    if not query or not text:
        return 0.0
    q = query.lower()
    t = text.lower()
    # 英文单词重叠
    q_words = set(re.findall(r"[a-z0-9_]{2,}", q))
    if q_words:
        w = sum(1 for w_ in q_words if w_ in t) / len(q_words)
    else:
        w = 0.0
    # 中文字符（2 字以上）重叠
    q_cjk = re.findall(r"[\u4e00-\u9fff]", q)
    if len(q_cjk) >= 2:
        c = sum(1 for ch in set(q_cjk) if ch in t) / len(set(q_cjk))
    else:
        c = 0.0
    return min(1.0, max(w, c * 0.9))


def _recency_score(time_val: datetime | None, now: datetime) -> float:
    """时效分：指数衰减，1.0（今天）→ ~0.5（60 天）→ 0（很久以前）。"""
    if not time_val:
        return 0.3
    try:
        days = max(0.0, (now - time_val).total_seconds() / 86400.0)
    except Exception:  # noqa: BLE001
        return 0.3
    return math.exp(-days / RECENCY_HALF_DAYS)


class MemoryService:
    def __init__(
        self,
        embedding: LlamaServer | None,
        vectors: VectorStore,
        session_factory: sessionmaker,
        top_k: int = 5,
        min_score: float = 0.35,
        dedup_threshold: float = 0.95,
        embed_fn: Callable[[str], list[float]] | None = None,
        llm: LlamaServer | None = None,
    ):
        self.embedding = embedding
        self.vectors = vectors
        self.session_factory = session_factory
        self.top_k = top_k
        self.min_score = min_score
        self.dedup_threshold = dedup_threshold
        # 允许外部注入 embed_fn（测试/降级）；默认走 embedding 服务
        self._embed_fn = embed_fn
        # HyDE 需要聊天 LLM（仅生成假设文档，不额外占模型内存）
        self.llm = llm

    @property
    def enabled(self) -> bool:
        return self.embedding is not None and self.embedding.ready

    # ---------- 向量化 ----------

    def embed_text(self, text: str) -> list[float] | None:
        if self._embed_fn is not None:
            try:
                return self._embed_fn(text)
            except Exception as exc:  # noqa: BLE001
                log.warning("embed_fn 失败: %s", exc)
                return None
        if not self.enabled:
            return None
        try:
            return self.embedding.embed([text])[0]
        except Exception as exc:  # noqa: BLE001
            log.warning("向量化失败: %s", exc)
            return None

    # ---------- 入库 ----------

    def remember(
        self,
        text: str,
        source: str = "chat",
        app: str = "mindtrace",
        importance: float = 0.5,
        project: str | None = None,
        activity: str | None = None,
    ) -> Event | None:
        """创建事件并向量化（向量级去重：极高相似则合并重要度）。失败不阻塞主流程。"""
        vec = self.embed_text(text)
        if vec is not None and self.dedup_threshold > 0:
            hits = self.vectors.search(vec, top_k=1, min_score=self.dedup_threshold)
            if hits and hits[0][0] == ENTITY_EVENT:
                # 去重合并：保留双方时间戳与来源（"该经验来自 A 与 B 两次事件"）
                db = self.session_factory()
                try:
                    old = db.get(Event, hits[0][1])
                    if old is not None:
                        old.importance = max(old.importance, importance)
                        if source != "chat":
                            old.source = source
                        provenance = list(old.objects or [])
                        provenance.append({
                            "time": datetime.now().isoformat(),
                            "source": source,
                            "text": text[:120],
                        })
                        old.objects = provenance[-10:]
                        db.commit()
                        return old
                except Exception:  # noqa: BLE001
                    db.rollback()
                finally:
                    db.close()

        db = self.session_factory()
        try:
            ev = Event(
                time=datetime.now(),
                app=app,
                activity=activity or text[:500],
                intent=text[:500],
                project=project,
                importance=importance,
                source=source,
            )
            db.add(ev)
            db.commit()
            db.refresh(ev)
        except Exception as exc:  # noqa: BLE001
            log.warning("事件入库失败: %s", exc)
            db.rollback()
            return None
        finally:
            db.close()

        if vec is not None:
            try:
                self.vectors.add(ENTITY_EVENT, ev.id, vec)
            except Exception as exc:  # noqa: BLE001
                log.warning("向量入库失败: %s", exc)
        return ev

    # ---------- 检索（HyDE + 融合重排） ----------

    def _hyde_doc(self, query: str) -> str | None:
        """HyDE：让 LLM 生成一段"假设的用户经历记录"，扩充查询语义。

        模糊表述（如"那个蓝色图标的项目"）经此展开后，向量召回显著变准。
        """
        if self.llm is None or not self.llm.ready:
            return None
        prompt = (
            "你是用户的个人日志整理助手。根据下面的回忆性问题，"
            "写一段 40-80 字、像用户本人日记/工作记录风格的文字，"
            "描述用户可能经历过的相关事情（含具体名词、项目、方案）。\n"
            f"问题：{query}\n只输出这段假设记录，不要解释。"
        )
        try:
            doc = self.llm.chat(
                [{"role": "system", "content": "你只输出假设的记录文本，不加任何解释。"},
                 {"role": "user", "content": prompt}],
                temperature=0.4,
            ).strip()
            return doc[:200] if doc else None
        except Exception as exc:  # noqa: BLE001
            log.debug("HyDE 生成失败: %s", exc)
            return None

    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """HyDE 双路召回 + 多信号融合重排。

        流程：原查询 + HyDE 假设文档 各自向量召回（更大 K）→ 合并候选 →
        按 向量/关键词/时效/重要度 融合打分重排 → 返回 top_k。
        """
        k = top_k or self.top_k
        recall_k = max(k * 3, 12)
        hits_map: dict[tuple[str, int], float] = {}  # (etype, eid) -> 最高向量分

        # HyDE：LLM 生成假设记录，与原查询组成双路召回
        query_vec = self.embed_text(query)
        hyde_doc = self._hyde_doc(query) if query_vec is not None else None
        hyde_vec = self.embed_text(hyde_doc) if hyde_doc else None
        for vec in (query_vec, hyde_vec):
            if vec is None:
                continue
            try:
                found = self.vectors.search(vec, top_k=recall_k, min_score=self.min_score * 0.8)
            except Exception as exc:  # noqa: BLE001
                log.warning("向量检索失败: %s", exc)
                continue
            for etype, eid, score in found:
                key = (etype, eid)
                if key not in hits_map or score > hits_map[key]:
                    hits_map[key] = score
        if not hits_map:
            return []

        db = self.session_factory()
        now = datetime.now()
        try:
            rows: list[dict] = []
            for (etype, eid), vscore in hits_map.items():
                time_val = None
                text = ""
                importance = 0.4
                if etype == ENTITY_EVENT:
                    row = db.get(Event, eid)
                    if row is None:
                        continue
                    time_val, text, importance = row.time, row.activity or row.intent or "", row.importance
                elif etype == ENTITY_EXPERIENCE:
                    row = db.get(Experience, eid)
                    if row is None:
                        continue
                    time_val, text = row.created_at, f"{row.problem} → {row.final_solution or ''}"
                    importance = row.importance
                else:
                    continue

                kw = _keyword_overlap(query, text)
                rec = _recency_score(time_val, now)
                imp_norm = min(1.0, max(0.0, (float(importance or 0) - 0.1) / 0.7))
                vec_norm = min(1.0, max(0.0, vscore / 0.75))
                fused = W_VEC * vec_norm + W_KW * kw + W_REC * rec + W_IMP * imp_norm
                rows.append({
                    "etype": etype, "eid": eid, "time": time_val,
                    "text": text, "importance": importance,
                    "score": round(fused, 4),
                    "vec": round(vscore, 3), "kw": round(kw, 3),
                })

            # 融合分重排，取 top_k
            rows.sort(key=lambda r: r["score"], reverse=True)
            rows = rows[:k]

            snippets: list[dict] = []
            for r in rows:
                if r["etype"] == ENTITY_EVENT:
                    snippets.append({
                        "kind": "event",
                        "tier": "B",  # 情景事件 = 证据分级 B
                        "id": r["eid"],
                        "time": r["time"].isoformat() if r["time"] else None,
                        "text": r["text"],
                        "app": None,
                        "project": None,
                        "importance": r["importance"],
                        "score": r["score"],
                    })
                else:
                    snippets.append({
                        "kind": "experience",
                        "tier": "A" if True else "B",  # 经验确认分级见下
                        "id": r["eid"],
                        "time": r["time"].isoformat() if r["time"] else None,
                        "text": r["text"],
                        "project": None,
                        "importance": r["importance"],
                        "score": r["score"],
                    })
            # 经验分级需查 confirm 状态
            for s in snippets:
                if s["kind"] == "experience":
                    row = db.get(Experience, s["id"])
                    if row is not None:
                        s["tier"] = "A" if row.confirmed else "B"
            return snippets
        finally:
            db.close()

    def build_context(self, query: str, max_snippets: int | None = None) -> tuple[list[dict], bool]:
        """组装带编号的记忆上下文。返回 (snippets, found)。"""
        snippets = self.search(query)
        if max_snippets:
            snippets = snippets[:max_snippets]
        return snippets, bool(snippets)

    # ---------- 经验 ----------

    def add_experience(
        self,
        problem: str,
        final_solution: str | None = None,
        background: str | None = None,
        scenarios: str | None = None,
        advice: str | None = None,
        tags: list[str] | None = None,
        confirmed: bool = False,
    ) -> Experience | None:
        db = self.session_factory()
        try:
            exp = Experience(
                problem=problem,
                background=background,
                final_solution=final_solution,
                scenarios=scenarios,
                advice=advice,
                tags=tags,
                confirmed=confirmed,
                status="confirmed" if confirmed else "pending",
            )
            db.add(exp)
            db.commit()
            db.refresh(exp)
        except Exception as exc:  # noqa: BLE001
            log.warning("经验入库失败: %s", exc)
            db.rollback()
            return None
        finally:
            db.close()

        vec = self.embed_text(f"{problem} {final_solution or ''}")
        if vec is not None:
            try:
                self.vectors.add(ENTITY_EXPERIENCE, exp.id, vec)
            except Exception as exc:  # noqa: BLE001
                log.warning("经验向量入库失败: %s", exc)
        return exp
