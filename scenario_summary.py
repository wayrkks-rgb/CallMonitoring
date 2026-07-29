# -*- coding: utf-8 -*-
"""
scenario_summary.py — scenario_parser 의 그래프를 '설명서/지도' 화면용 데이터로 가공.

전부 XML 에서 결정적으로 추출 (LLM 없음):
  1) 단계별 흐름     : 시작 노드부터 링크를 따라 정렬한 노드 순서
  2) 분기 조건       : IfNode 의 Condition + true/false 가 가는 대상
  3) 사용하는 서비스 : CallPage/GotoPage 의 TargetPage 목록
  4) 블록 설정값     : 스크립트의 `app.X = "v"; // 한글주석` 추출 (작성자 주석 = 권위 있는 설명)
"""
import re
import xml.etree.ElementTree as ET

from scenario_parser import parse_page

# app.변수 = 값 ; // 주석   패턴
_SET_RE = re.compile(r'(app\.\w+)\s*=\s*([^;/\n]+?)\s*;?\s*//\s*(.+)')

# 흐름 정렬 시 우선적으로 따라갈 엣지 라벨 (주 경로 우선)
_PRIMARY = ("ok", "true", "", "next")


def _order_flow(nodes, edges, by_id):
    """시작 노드부터 DFS(주경로 우선)로 방문 순서를 만든다."""
    out = {}
    for e in edges:
        out.setdefault(e["from"], []).append(e)

    def rank(lbl):
        lbl = (lbl or "").lower()
        return _PRIMARY.index(lbl) if lbl in _PRIMARY else len(_PRIMARY)

    start = next((n for n in nodes if n["type"] == "StartNode"), nodes[0] if nodes else None)
    order, seen = [], set()
    stack = [start["id"]] if start else []
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in by_id:
            continue
        seen.add(nid)
        order.append(nid)
        # 주 경로를 마지막에 push → LIFO 라 먼저 방문
        nexts = sorted(out.get(nid, []), key=lambda e: rank(e["label"]), reverse=True)
        for e in nexts:
            if e["to"] not in seen:
                stack.append(e["to"])
    # 시작에서 도달 못한 고립 노드도 뒤에 붙인다
    for n in nodes:
        if n["id"] not in seen:
            order.append(n["id"])
    return order, out


def build_summary(path):
    g = parse_page(path)
    nodes, edges = g["nodes"], g["edges"]
    by_id = {n["id"]: n for n in nodes}
    order, out = _order_flow(nodes, edges, by_id)

    def lbl(nid):
        n = by_id.get(nid)
        return (n["label"] or n["type"]) if n else "?"

    # 1) 단계별 흐름
    flow = []
    for nid in order:
        n = by_id[nid]
        goes = [(e["label"], lbl(e["to"])) for e in out.get(nid, [])]
        flow.append({
            "label": n["label"] or n["type"],
            "kind": n["kind"], "type": n["type"], "seq": n["seq"],
            "goes_to": goes,
        })

    # 2) 분기 조건
    branches = []
    for n in nodes:
        if not n.get("condition"):
            continue
        routes = [(e["label"], lbl(e["to"])) for e in out.get(n["id"], [])]
        branches.append({"at": n["label"], "cond": n["condition"], "routes": routes})

    # 3) 사용하는 서비스/서브
    resources, seen_tp = [], set()
    for n in nodes:
        tp = n.get("target_page")
        if not tp:
            continue
        role = ("호출(복귀O)" if n["type"] == "CallPageNode"
                else "이동(복귀X)" if n["type"] == "GotoPageNode" else "참조")
        key = (tp, n["label"])
        if key in seen_tp:
            continue
        seen_tp.add(key)
        resources.append({"from": n["label"], "target": tp, "role": role})

    # 4) 블록 설정값 (스크립트 주석)
    settings = []
    root = ET.parse(path).getroot()
    id_seq = 0
    for node in root.findall("./Nodes/Node"):
        cp = node.find("CustomProperties")
        if cp is None:
            continue
        block = re.sub(r"\s+", " ", (node.findtext("Text") or "").strip())
        pairs = []
        for tag in ("Script", "PreScript"):
            body = cp.findtext(tag) or ""
            for var, val, cmt in _SET_RE.findall(body):
                pairs.append([var, val.strip().strip('"'), cmt.strip()])
        if pairs:
            # 같은 변수 중복(음성/WebVoice 분기 등) 제거
            uniq, keys = [], set()
            for p in pairs:
                k = (p[0], p[2])
                if k not in keys:
                    keys.add(k)
                    uniq.append(p)
            settings.append({"block": block, "sets": uniq[:15]})

    return {
        "page": g["page"],
        "flow": flow,
        "branches": branches,
        "resources": resources,
        "settings": settings,
        "stats": {"nodes": len(nodes), "edges": len(edges),
                  "branches": len(branches), "resources": len(resources)},
    }


if __name__ == "__main__":
    import sys
    import json
    s = build_summary(sys.argv[1] if len(sys.argv) > 1 else "scenario_cache/운영/_hangup.xml")
    print(json.dumps(s, ensure_ascii=False, indent=2))
