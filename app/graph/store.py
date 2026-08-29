"""知识图谱存储：NetworkX 内存图 + SQLite 持久化（graph_nodes / graph_edges）。"""
from __future__ import annotations

import logging
from datetime import datetime

import networkx as nx

from app.core.models import GraphEdge, GraphNode

log = logging.getLogger("mindtrace.graph.store")


class GraphStore:
    def __init__(self, session_factory):
        self.session_factory = session_factory
        self.graph: nx.DiGraph = nx.DiGraph()
        self._id_map: dict[tuple[str, str], int] = {}
        self._loaded = False

    # ---------- 加载 ----------

    def load(self) -> None:
        db = self.session_factory()
        try:
            for n in db.query(GraphNode).all():
                self.graph.add_node(n.id, type=n.type, name=n.name, summary=n.summary,
                                    time=n.time, meta=n.meta)
                self._id_map[(n.type, n.name.strip())] = n.id
            for e in db.query(GraphEdge).all():
                self.graph.add_edge(e.src_node_id, e.dst_node_id, id=e.id, relation=e.relation,
                                    time=e.time, confidence=e.confidence, weight=e.weight)
        finally:
            db.close()
        self._loaded = True
        log.info("图谱已加载：%d 节点 / %d 边", self.graph.number_of_nodes(), self.graph.number_of_edges())

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ---------- 写入 ----------

    def get_or_create_node(self, ntype: str, name: str, summary: str | None = None,
                           meta: dict | None = None, time: datetime | None = None) -> int:
        self.ensure_loaded()
        name = (name or "").strip()
        if not name:
            name = "未命名"
        key = (ntype, name)
        if key in self._id_map:
            return self._id_map[key]
        db = self.session_factory()
        try:
            node = GraphNode(type=ntype, name=name[:500], summary=(summary or "")[:1000],
                             meta=meta, time=time)
            db.add(node)
            db.commit()
            db.refresh(node)
            node_id = node.id
        finally:
            db.close()
        self.graph.add_node(node_id, type=ntype, name=name, summary=summary, time=time, meta=meta)
        self._id_map[key] = node_id
        return node_id

    def add_edge(self, src: int, dst: int, relation: str, time: datetime | None = None,
                 confidence: float = 0.8, weight: float = 1.0) -> None:
        self.ensure_loaded()
        if self.graph.has_edge(src, dst):
            old = self.graph.edges[src, dst]
            if old.get("relation") == relation:
                # 同边更新置信度（取最大）
                new_conf = max(float(old.get("confidence", 0) or 0), confidence)
                self.graph.edges[src, dst]["confidence"] = new_conf
                if old.get("time") is None and time is not None:
                    self.graph.edges[src, dst]["time"] = time
                db = self.session_factory()
                try:
                    e = db.get(GraphEdge, old.get("id"))
                    if e is not None:
                        e.confidence = new_conf
                        if e.time is None and time is not None:
                            e.time = time
                        db.commit()
                finally:
                    db.close()
                return
        db = self.session_factory()
        try:
            edge = GraphEdge(src_node_id=src, dst_node_id=dst, relation=relation, time=time,
                             confidence=confidence, weight=weight)
            db.add(edge)
            db.commit()
            db.refresh(edge)
            eid = edge.id
        finally:
            db.close()
        self.graph.add_edge(src, dst, id=eid, relation=relation, time=time,
                            confidence=confidence, weight=weight)

    # ---------- 查询 ----------

    def node(self, node_id: int) -> dict | None:
        self.ensure_loaded()
        if node_id not in self.graph:
            return None
        d = dict(self.graph.nodes[node_id])
        d["id"] = node_id
        return d

    def find_nodes(self, query: str, ntype: str | None = None, limit: int = 10) -> list[dict]:
        """按名称子串查找节点（近似实体识别）。"""
        self.ensure_loaded()
        q = query.strip().lower()
        out = []
        for nid, data in self.graph.nodes(data=True):
            if ntype and data.get("type") != ntype:
                continue
            name = str(data.get("name", ""))
            if q and q in name.lower():
                out.append({"id": nid, "type": data.get("type"), "name": name,
                            "summary": data.get("summary")})
        return out[:limit]

    def neighbors(self, node_id: int, hops: int = 1, relation: str | None = None,
                  max_results: int = 50) -> list[dict]:
        self.ensure_loaded()
        if node_id not in self.graph:
            return []
        result: dict[int, dict] = {}

        def _walk(current: int, depth: int, path: list):
            if depth > hops:
                return
            for neighbor in list(self.graph.successors(current)) + list(self.graph.predecessors(current)):
                if neighbor in result:
                    continue
                data = self.graph.nodes[neighbor]
                edges = []
                if self.graph.has_edge(current, neighbor):
                    edges.append(self.graph.edges[current, neighbor])
                if self.graph.has_edge(neighbor, current):
                    edges.append(self.graph.edges[neighbor, current])
                if relation and not any(e.get("relation") == relation for e in edges):
                    continue
                result[neighbor] = {
                    "id": neighbor, "type": data.get("type"), "name": data.get("name"),
                    "summary": data.get("summary"), "hop": depth,
                    "relations": [e.get("relation") for e in edges],
                    "confidence": max((float(e.get("confidence", 0) or 0) for e in edges), default=0.8),
                }
                _walk(neighbor, depth + 1, path + [neighbor])

        _walk(node_id, 1, [node_id])
        return sorted(result.values(), key=lambda x: x["hop"])[:max_results]

    def path_between(self, src_id: int, dst_id: int, max_hops: int = 4) -> list[dict]:
        """多跳路径（无向可达），按 边置信度乘积 × 跳数衰减 打分（低置信长链降级）。"""
        self.ensure_loaded()
        if src_id not in self.graph or dst_id not in self.graph:
            return []
        # 语义边非严格因果流：用无向图求可达，边方向信息保留在结果中
        undirected = self.graph.to_undirected()
        paths = []
        try:
            for p in nx.all_simple_paths(undirected, src_id, dst_id, cutoff=max_hops):
                score = 1.0
                edges = []
                for a, b in zip(p, p[1:]):
                    if self.graph.has_edge(a, b):
                        e = self.graph.edges[a, b]
                        direction = "→"
                    else:
                        e = self.graph.edges[b, a]
                        direction = "←"
                    conf = float(e.get("confidence", 0.8) or 0.8)
                    score *= conf
                    edges.append({
                        "from": a, "to": b, "relation": e.get("relation"),
                        "confidence": round(conf, 3), "time": e.get("time"), "direction": direction,
                    })
                score *= (0.85 ** (len(p) - 2))  # 跳数衰减
                paths.append({
                    "nodes": p,
                    "edges": edges,
                    "hops": len(p) - 1,
                    "score": round(score, 4),
                })
        except nx.NetworkXNoPath:
            return []
        paths.sort(key=lambda x: -x["score"])
        return paths[:5]

    def subgraph_for_view(self, limit: int = 120, ntype: str | None = None) -> dict:
        """前端可视化用：节点+边（限制规模）。"""
        self.ensure_loaded()
        nodes, edges, seen = [], [], set()
        for nid, data in self.graph.nodes(data=True):
            if ntype and data.get("type") != ntype:
                continue
            nodes.append({"id": nid, "type": data.get("type"), "name": data.get("name"),
                          "summary": data.get("summary"), "time": str(data.get("time") or "")[:10]})
            seen.add(nid)
            if len(nodes) >= limit:
                break
        for a, b, data in self.graph.edges(data=True):
            if a in seen and b in seen:
                edges.append({"from": a, "to": b, "relation": data.get("relation"),
                              "confidence": data.get("confidence"),
                              "time": str(data.get("time") or "")[:10]})
        return {"nodes": nodes, "edges": edges}

    def counts(self) -> dict:
        self.ensure_loaded()
        types: dict[str, int] = {}
        relations: dict[str, int] = {}
        for _, data in self.graph.nodes(data=True):
            t = data.get("type", "?")
            types[t] = types.get(t, 0) + 1
        for _, _, data in self.graph.edges(data=True):
            r = data.get("relation", "?")
            relations[r] = relations.get(r, 0) + 1
        return {"nodes": len(self.graph.nodes), "edges": len(self.graph.edges),
                "types": types, "relations": relations}

    def count_edges_for_node(self, node_id: int) -> int:
        self.ensure_loaded()
        if node_id not in self.graph:
            return 0
        return self.graph.degree(node_id)

    # ---------- 合并 ----------

    # ---------- 编辑（手动维护） ----------

    def add_node(self, ntype: str, name: str, summary: str | None = None,
                 meta: dict | None = None, time: datetime | None = None) -> int:
        """手动新增节点（允许重复名；返回新节点 id）。"""
        self.ensure_loaded()
        name = (name or "").strip() or "未命名"
        db = self.session_factory()
        try:
            node = GraphNode(type=ntype or "project", name=name[:500],
                             summary=(summary or "")[:1000], meta=meta, time=time)
            db.add(node)
            db.commit()
            db.refresh(node)
            node_id = node.id
        finally:
            db.close()
        self.graph.add_node(node_id, type=ntype or "project", name=name,
                            summary=summary, time=time, meta=meta)
        self._id_map[(node.type, node.name)] = node_id
        return node_id

    def update_node(self, node_id: int, *, name: str | None = None,
                    summary: str | None = None, ntype: str | None = None,
                    meta: dict | None = None) -> bool:
        """编辑节点属性（name/summary/type/meta），DB + 内存同步。返回是否成功。"""
        self.ensure_loaded()
        if node_id not in self.graph:
            return False
        db = self.session_factory()
        try:
            node = db.get(GraphNode, node_id)
            if node is None:
                return False
            if name is not None and (name or "").strip():
                old_key = (node.type, node.name)
                self._id_map.pop(old_key, None)
                node.name = name.strip()[:500]
            if summary is not None:
                node.summary = summary[:1000]
            if ntype is not None:
                node.type = ntype[:32]
            if meta is not None:
                node.meta = meta
            db.commit()
        finally:
            db.close()
        # 同步内存图
        data = self.graph.nodes[node_id]
        if name is not None and (name or "").strip():
            data["name"] = name.strip()[:500]
        if summary is not None:
            data["summary"] = summary
        if ntype is not None:
            data["type"] = ntype
        if meta is not None:
            data["meta"] = meta
        self._id_map[(data.get("type"), data.get("name"))] = node_id
        return True

    def delete_node(self, node_id: int) -> bool:
        """删除节点及其全部关联边。返回是否删除。"""
        self.ensure_loaded()
        if node_id not in self.graph:
            return False
        db = self.session_factory()
        try:
            db.query(GraphEdge).filter(
                (GraphEdge.src_node_id == node_id) | (GraphEdge.dst_node_id == node_id)
            ).delete(synchronize_session=False)
            db.delete(db.get(GraphNode, node_id))
            db.commit()
        finally:
            db.close()
        data = self.graph.nodes[node_id]
        self._id_map.pop((data.get("type"), data.get("name")), None)
        self.graph.remove_node(node_id)
        return True

    def update_edge(self, edge_id: int, *, relation: str | None = None,
                    confidence: float | None = None) -> bool:
        """编辑边（关系类型/置信度）。返回是否成功。"""
        self.ensure_loaded()
        # 找到该 edge_id 对应的内存边
        target = None
        for a, b, data in self.graph.edges(data=True):
            if data.get("id") == edge_id:
                target = (a, b, data)
                break
        if target is None:
            return False
        a, b, data = target
        db = self.session_factory()
        try:
            e = db.get(GraphEdge, edge_id)
            if e is None:
                return False
            if relation is not None and relation.strip():
                e.relation = relation.strip()[:32]
            if confidence is not None:
                e.confidence = max(0.0, min(1.0, confidence))
            db.commit()
        finally:
            db.close()
        if relation is not None and relation.strip():
            data["relation"] = relation.strip()[:32]
        if confidence is not None:
            data["confidence"] = max(0.0, min(1.0, confidence))
        return True

    def delete_edge(self, edge_id: int) -> bool:
        """删除边。返回是否删除。"""
        self.ensure_loaded()
        target = None
        for a, b, data in self.graph.edges(data=True):
            if data.get("id") == edge_id:
                target = (a, b)
                break
        if target is None:
            return False
        db = self.session_factory()
        try:
            e = db.get(GraphEdge, edge_id)
            if e is not None:
                db.delete(e)
                db.commit()
        finally:
            db.close()
        self.graph.remove_edge(*target)
        return True

    def merge_nodes(self, keep_id: int, drop_id: int) -> None:
        """合并重复节点：drop 的边全部迁移到 keep。"""
        self.ensure_loaded()
        if keep_id == drop_id or drop_id not in self.graph:
            return
        db = self.session_factory()
        try:
            # 迁移边（DB）
            for e in db.query(GraphEdge).filter(
                (GraphEdge.src_node_id == drop_id) | (GraphEdge.dst_node_id == drop_id)
            ).all():
                src = keep_id if e.src_node_id == drop_id else e.src_node_id
                dst = keep_id if e.dst_node_id == drop_id else e.dst_node_id
                if src == dst:
                    db.delete(e)
                    continue
                dup = db.query(GraphEdge).filter(
                    GraphEdge.src_node_id == src, GraphEdge.dst_node_id == dst,
                    GraphEdge.relation == e.relation,
                ).first()
                if dup:
                    dup.confidence = max(dup.confidence, e.confidence)
                    db.delete(e)
                else:
                    e.src_node_id = src
                    e.dst_node_id = dst
            db.delete(db.get(GraphNode, drop_id))
            db.commit()
        finally:
            db.close()
        # 内存图重建（简单可靠）
        self._loaded = False
        self.graph = nx.DiGraph()
        self._id_map = {}
        self.load()

    def merge_duplicates(self) -> int:
        """合并同类型下名称归一化（去空白/大小写）相同的节点。返回合并数。"""
        self.ensure_loaded()
        groups: dict[tuple[str, str], list[int]] = {}
        for nid, data in self.graph.nodes(data=True):
            norm = str(data.get("name", "")).strip().lower()
            if not norm:
                continue
            groups.setdefault((data.get("type", "?"), norm), []).append(nid)
        merged = 0
        for _key, ids in groups.items():
            if len(ids) > 1:
                keep = ids[0]
                for drop in ids[1:]:
                    self.merge_nodes(keep, drop)
                    merged += 1
        if merged:
            log.info("图谱合并重复节点：%d", merged)
        return merged
