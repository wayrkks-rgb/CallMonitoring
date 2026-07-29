# -*- coding: utf-8 -*-
"""
scenario_parser.py — 한솔 IS-IVR 시나리오 디자이너(.dxml/.xml) → 그래프(JSON) 변환기

- 표준 라이브러리(xml.etree)만 사용 → 폐쇄망 무의존
- 한 페이지(.xml) → {nodes[], edges[]} 로 변환
- CallPageNode/GotoPageNode 의 <TargetPage> 를 따라가면 서브 시나리오까지 end-to-end 연결
"""
import os
import re
import xml.etree.ElementTree as ET

# NodeType → (표시 카테고리, 색상)  ※ 기존 로그 카테고리 색상 체계와 통일
NODE_KIND = {
    "StartNode":        ("시작",        "#28a745"),
    "StopSmartIVRNode": ("종료",        "#dc3545"),
    "StopNode":         ("종료",        "#dc3545"),
    "IfNode":           ("분기",        "#6c757d"),
    "ScriptNode":       ("스크립트",    "#17a2b8"),
    "CallPageNode":     ("서브호출",    "#6f42c1"),  # TargetPage 진입 후 복귀
    "GotoPageNode":     ("페이지이동",  "#007bff"),  # TargetPage 이동(복귀X)
    "PlayNode":         ("멘트",        "#fd7e14"),
    "GetDigitNode":     ("입력",        "#fd7e14"),
}

_WS = re.compile(r"\s+")


def _clean(s):
    """라벨의 줄바꿈/연속공백을 단일 공백으로 정리."""
    return _WS.sub(" ", (s or "").strip())


def _txt(node, tag, default=""):
    el = node.find(tag) if node is not None else None
    return el.text.strip() if (el is not None and el.text) else default


def parse_page(path):
    """단일 .xml(dxml) 페이지 → {page, nodes, edges}"""
    tree = ET.parse(path)
    root = tree.getroot()
    page_name = os.path.basename(path)

    nodes = []
    for n in root.findall("./Nodes/Node"):
        nid = n.get("Id")
        ntype = n.get("NodeType")
        cp = n.find("CustomProperties")
        kind, color = NODE_KIND.get(ntype, ("기타", "#adb5bd"))

        node = {
            "id": nid,                       # 링크 연결용(내부 Id)
            "type": ntype,
            "kind": kind,
            "color": color,
            "label": _clean(_txt(n, "Text")),
            "seq": _txt(cp, "Sequence") if cp is not None else "",  # 디자이너 표시 ID = 로그 블록ID
        }
        if cp is not None:
            cond = _txt(cp, "Condition")
            tgt = _txt(cp, "TargetPage")
            if cond:
                node["condition"] = cond
            if tgt:
                node["target_page"] = tgt    # ← 서브 시나리오 참조(핵심)
            tnid = _txt(cp, "TargetNodeId")
            if tnid and tnid != "99999999":
                node["target_node_id"] = tnid
            if _txt(cp, "Script"):
                node["has_script"] = True
            if _txt(cp, "PreScript"):
                node["has_prescript"] = True
        nodes.append(node)

    edges = []
    for lk in root.findall("./Links/Link"):
        o = lk.find("Origin")
        d = lk.find("Destination")
        if o is None or d is None:
            continue
        edges.append({
            "id": lk.get("Id"),
            "from": o.get("Id"),
            "to": d.get("Id"),
            "label": _clean(_txt(lk, "Text")),   # ok / true / false / timeout ...
        })

    return {"page": page_name, "nodes": nodes, "edges": edges}


if __name__ == "__main__":
    import json
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else "scenario_cache/운영/_hangup.xml"
    print(json.dumps(parse_page(p), ensure_ascii=False, indent=2))
