"""知识图谱 API：概要 / 节点 / 邻域 / 路径 / 可视化数据 / 手动编辑。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["graph"])


def _store(request: Request):
    s = getattr(request.app.state, "graph", None)
    if s is None:
        raise HTTPException(status_code=503, detail="图谱未启用")
    return s


# ---------- 编辑模型 ----------

class NodeEdit(BaseModel):
    name: str | None = None
    summary: str | None = None
    ntype: str | None = None
    meta: dict | None = None


class NodeCreate(BaseModel):
    name: str
    ntype: str = "project"
    summary: str | None = None


class EdgeEdit(BaseModel):
    relation: str | None = None
    confidence: float | None = None


class EdgeCreate(BaseModel):
    src: int
    dst: int
    relation: str
    confidence: float = 0.8


@router.post("/api/graph/nodes")
def graph_add_node(req: NodeCreate, request: Request):
    """手动新增节点。"""
    node_id = _store(request).add_node(req.ntype, req.name, req.summary)
    return {"ok": True, "id": node_id}


@router.patch("/api/graph/node/{node_id}")
def graph_edit_node(node_id: int, req: NodeEdit, request: Request):
    """编辑节点（名称/摘要/类型/meta）。"""
    if not _store(request).update_node(
        node_id, name=req.name, summary=req.summary, ntype=req.ntype, meta=req.meta
    ):
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"ok": True}


@router.delete("/api/graph/node/{node_id}")
def graph_delete_node(node_id: int, request: Request):
    """删除节点及其全部关联边。"""
    if not _store(request).delete_node(node_id):
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"ok": True}


@router.post("/api/graph/edges")
def graph_add_edge(req: EdgeCreate, request: Request):
    """手动新增边（关系）。"""
    _store(request).add_edge(req.src, req.dst, req.relation,
                             time=datetime.now(), confidence=req.confidence)
    return {"ok": True}


@router.patch("/api/graph/edge/{edge_id}")
def graph_edit_edge(edge_id: int, req: EdgeEdit, request: Request):
    """编辑边（关系类型/置信度）。"""
    if not _store(request).update_edge(edge_id, relation=req.relation,
                                       confidence=req.confidence):
        raise HTTPException(status_code=404, detail="边不存在")
    return {"ok": True}


@router.delete("/api/graph/edge/{edge_id}")
def graph_delete_edge(edge_id: int, request: Request):
    """删除边。"""
    if not _store(request).delete_edge(edge_id):
        raise HTTPException(status_code=404, detail="边不存在")
    return {"ok": True}


@router.get("/api/graph/summary")
def graph_summary(request: Request):
    return _store(request).counts()


@router.get("/api/graph/nodes")
def graph_nodes(request: Request, q: str = "", ntype: str | None = None, limit: int = 50):
    store = _store(request)
    if q:
        return {"nodes": store.find_nodes(q, ntype=ntype, limit=limit)}
    store.ensure_loaded()
    nodes = []
    for nid, data in list(store.graph.nodes(data=True))[:limit]:
        if ntype and data.get("type") != ntype:
            continue
        nodes.append({"id": nid, "type": data.get("type"), "name": data.get("name"),
                      "summary": data.get("summary"), "time": str(data.get("time") or "")[:10]})
    return {"nodes": nodes}


@router.get("/api/graph/node/{node_id}")
def graph_node(node_id: int, request: Request):
    n = _store(request).node(node_id)
    if n is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    return n


@router.get("/api/graph/node/{node_id}/neighbors")
def graph_neighbors(node_id: int, request: Request, hops: int = 1, relation: str | None = None):
    return {"neighbors": _store(request).neighbors(node_id, hops=min(hops, 3), relation=relation)}


@router.get("/api/graph/path")
def graph_path(request: Request, from_id: int, to_id: int, max_hops: int = 4):
    paths = _store(request).path_between(from_id, to_id, max_hops=min(max_hops, 6))
    if not paths:
        raise HTTPException(status_code=404, detail="未找到可达路径")
    return {"paths": paths}


@router.get("/api/graph/view")
def graph_view(request: Request, limit: int = 150, ntype: str | None = None):
    return _store(request).subgraph_for_view(limit=limit, ntype=ntype)


@router.post("/api/graph/sync")
def graph_sync(request: Request):
    """手动触发增量构建（构建器在服务端）。"""
    builder = getattr(request.app.state, "graph_builder", None)
    if builder is None:
        raise HTTPException(status_code=503, detail="图谱构建器未启用")
    stats = builder.sync_all()
    return {"ok": True, "stats": stats}
