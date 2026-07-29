# -*- coding: utf-8 -*-
"""
scenario_flow.py — 시나리오 그래프에서 '핵심 업무 흐름'만 규칙으로 추출.

- 예외 엣지(error/timeout/default/-1) 와 예외 노드(오류종료/재입력/잘못/상담원연결/에러)를
  본줄기에서 빼고, 각 단계의 '예외' 주석으로 내린다.
- 시작에서 정상(ok/true) 경로를 우선 따라가며 방문 순서를 만든다.
- milestone(시작/종료/서브호출/분기)과 minor(스크립트/기타/이동)를 구분해
  화면이 위계를 줄 수 있게 한다.
전부 XML 기반, LLM 없음.
"""
import re

from scenario_parser import parse_page

EXC_EDGE = {"error", "timeout", "default", "-1", "-2", "err", "fail"}
EXC_NODE = re.compile(
    r"(재입력|잘못\s*눌|오류\s*종료|^\d+\s*오류|상담원|점검\s*중|사용\s*불가|불가\s*안내|"
    r"에러\s*메[세시]지|errormsg|초기메뉴|(?<![가-힣])재신청)", re.I)
# 판정/처리 노드는 이름에 '에러/오류'가 있어도 정상 업무 분기 (예: '에러코드 체크')
NON_EXC_TYPES = {"IfNode", "SwitchNode", "ScriptNode", "GetDigitPromptNode"}
# 정상 완료 노드 — 어떤 엣지로 오든 예외 아님 (멘트 후 timeout 종료 등)
NORMAL_END = re.compile(r"(감사합니다|이용해\s*주셔|정상\s*종료|정상\s*접수|처리\s*완료|접수\s*완료)", re.I)
MILESTONE = {"시작", "종료", "서브호출", "분기"}

_RANK = {"ok": 0, "true": 1, "": 2, "next": 2, "false": 4, "barge-in": 6}


def _rank(lbl):
    return _RANK.get((lbl or "").strip().lower(), 3)


def build_core_flow(path):
    g = parse_page(path)
    by_id = {n["id"]: n for n in g["nodes"]}
    out = {}
    for e in g["edges"]:
        out.setdefault(e["from"], []).append(e)

    def label_of(nid):
        n = by_id.get(nid)
        return (n["label"] or n["type"]) if n else "?"

    def is_exc_edge(e):
        t = by_id.get(e["to"])
        # 정상 완료 안내/종료로 가는 엣지는 라벨(timeout 등)과 무관하게 정상 흐름
        if t and NORMAL_END.search(t["label"] or ""):
            return False
        if (e["label"] or "").strip().lower() in EXC_EDGE:
            return True
        if not t:
            return False
        if t.get("type") in NON_EXC_TYPES:      # 판정/처리 노드는 정상 흐름
            return False
        return bool(EXC_NODE.search(t["label"] or ""))

    # 정상 경로 우선 방문 순서
    start = next((n for n in g["nodes"] if n["type"] == "StartNode"),
                 g["nodes"][0] if g["nodes"] else None)
    order, seen = [], set()
    stack = [start["id"]] if start else []
    while stack:
        nid = stack.pop()
        if nid in seen or nid not in by_id:
            continue
        seen.add(nid)
        order.append(nid)
        outs = out.get(nid, [])
        mains = [e for e in outs if not is_exc_edge(e)]
        if not mains:
            # 주경로가 없으면: 예외 '대상'이 아닌 엣지를 정상 연결로 간주
            # (CallPage 의 default 반환 등이 유일 경로인 경우)
            mains = [e for e in outs
                     if by_id.get(e["to"]) and
                     not EXC_NODE.search(by_id[e["to"]]["label"] or "")]
        for e in sorted(mains, key=lambda e: _rank(e["label"]), reverse=True):
            if e["to"] not in seen:
                stack.append(e["to"])

    steps = []
    for nid in order:
        n = by_id[nid]
        outs = out.get(nid, [])
        nexts = [(e["label"], label_of(e["to"])) for e in outs if not is_exc_edge(e)]
        excs = [(e["label"], label_of(e["to"])) for e in outs if is_exc_edge(e)]
        # 자기 자신 재방문(재입력 루프)·중복 제거
        excs = [x for x in dict.fromkeys(excs) if x[1] != n["label"]]
        steps.append({
            "label": n["label"] or n["type"],
            "kind": n["kind"], "type": n["type"], "seq": n["seq"],
            "milestone": (n["kind"] in MILESTONE
                          or n["type"] in ("ReturnPageNode", "StopNode", "HangupNode")
                          or bool(NORMAL_END.search(n["label"] or ""))),
            "sub": n.get("target_page", ""),
            "cond": n.get("condition", ""),
            "branch": len([x for x in nexts if x[0].lower() in ("true", "false")]) >= 2,
            "next": nexts, "exc": excs,
        })

    total = len(g["nodes"])
    core = len(steps)
    milestones = sum(1 for s in steps if s["milestone"])
    return {
        "page": g["page"], "steps": steps,
        "stats": {"total": total, "core": core, "hidden": total - core,
                  "milestones": milestones},
    }


if __name__ == "__main__":
    import sys
    cf = build_core_flow(sys.argv[1] if len(sys.argv) > 1 else "scenario_cache/운영/_hangup.xml")
    print(f"\n{cf['page']}  (전체 {cf['stats']['total']}노드 → 핵심 {cf['stats']['core']}, "
          f"이정표 {cf['stats']['milestones']}, 예외로 내림 {cf['stats']['hidden']})\n")
    for i, s in enumerate(cf["steps"], 1):
        m = "●" if s["milestone"] else "·"
        nx = "  ".join(f"{l or '→'}: {t}" for l, t in s["next"]) or "(종료)"
        print(f" {m} {i}. {s['label']}  [{s['kind']}]  {nx}")
        if s["exc"]:
            print(f"        예외: " + ", ".join(f"{l}→{t}" for l, t in s["exc"]))
