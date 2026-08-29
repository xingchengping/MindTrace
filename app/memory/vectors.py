"""向量存储：SQLite BLOB 持久化 + numpy 余弦检索。

Phase 1 选择：不用原生扩展（避免 sqlite-vec DLL 在沙箱环境的兼容风险），
向量以 float32 BLOB 存 SQLite 表，检索时 numpy 暴力余弦。
个人数据量（万级向量）下毫秒级，完全够用；
后续数据量增大需要 ANN 时再引入 sqlite-vec/FAISS，接口保持不变。
"""
from __future__ import annotations

import numpy as np
from sqlalchemy import create_engine, text


class VectorStore:
    def __init__(self, engine: create_engine):
        self.engine = engine
        self._init_table()

    def _init_table(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS vectors ("
                    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " entity_type TEXT NOT NULL,"
                    " entity_id INTEGER NOT NULL,"
                    " dim INTEGER NOT NULL,"
                    " vec BLOB NOT NULL,"
                    " created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_vectors_entity "
                    "ON vectors(entity_type, entity_id)"
                )
            )

    def add(self, entity_type: str, entity_id: int, vector) -> None:
        arr = np.asarray(vector, dtype=np.float32)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO vectors(entity_type, entity_id, dim, vec) "
                    "VALUES(:t, :e, :d, :v)"
                ),
                {"t": entity_type, "e": entity_id, "d": int(arr.shape[0]), "v": arr.tobytes()},
            )

    def delete_entity(self, entity_type: str, entity_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM vectors WHERE entity_type=:t AND entity_id=:e"),
                {"t": entity_type, "e": entity_id},
            )

    def count(self) -> int:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT COUNT(*) FROM vectors")).scalar_one()
        return int(row)

    def search(
        self, query_vec, top_k: int = 5, min_score: float = 0.0
    ) -> list[tuple[str, int, float]]:
        """返回 [(entity_type, entity_id, score)]，按分数降序。"""
        q = np.asarray(query_vec, dtype=np.float32)
        q_norm = float(np.linalg.norm(q))
        if q_norm < 1e-9:
            return []
        q = q / q_norm

        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT entity_type, entity_id, vec FROM vectors")
            ).fetchall()

        results: list[tuple[str, int, float]] = []
        for etype, eid, blob in rows:
            v = np.frombuffer(blob, dtype=np.float32)
            n = float(np.linalg.norm(v))
            if n < 1e-9:
                continue
            score = float(np.dot(q, v) / n)
            if score >= min_score:
                results.append((etype, eid, score))
        results.sort(key=lambda x: -x[2])
        return results[:top_k]
