"""Phase 3 图谱 API 测试。"""
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:4000"


def req(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main():
    d = req("POST", "/api/graph/sync")
    print("sync:", d.get("stats"))
    d = req("GET", "/api/graph/summary")
    print("summary:", d.get("nodes"), "nodes /", d.get("edges"), "edges")
    d = req("GET", "/api/graph/view?limit=50")
    print("view:", len(d.get("nodes", [])), "nodes,", len(d.get("edges", [])), "edges")

    d = req("GET", "/api/graph/nodes?q=" + urllib.parse.quote("向量"))
    print("search 向量:", [(n["type"], n["name"][:20]) for n in d.get("nodes", [])][:4])

    d = req("GET", "/api/graph/nodes?q=" + urllib.parse.quote("MindTrace") + "&ntype=project")
    if d.get("nodes"):
        pid = d["nodes"][0]["id"]
        d2 = req("GET", f"/api/graph/node/{pid}/neighbors?hops=2")
        print("project neighbors:")
        for n in d2.get("neighbors", [])[:6]:
            print("   ", n["type"], "|", n["name"][:18], "|", n["relations"])

    # 路径：问题 → 方案（因果链）
    d = req("GET", "/api/graph/nodes?q=" + urllib.parse.quote("原生扩展"))
    if d.get("nodes"):
        prob = d["nodes"][0]["id"]
        d2 = req("GET", "/api/graph/nodes?q=" + urllib.parse.quote("SQLite BLOB"))
        if d2.get("nodes"):
            sol = d2["nodes"][0]["id"]
            try:
                d3 = req("GET", f"/api/graph/path?from_id={prob}&to_id={sol}&max_hops=4")
                print("path 问题→方案:", [(p["hops"], p["score"], [e["relation"] for e in p["edges"]]) for p in d3.get("paths", [])][:3])
            except Exception as e:
                print("path:", e)


if __name__ == "__main__":
    main()
