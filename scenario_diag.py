# -*- coding: utf-8 -*-
"""
scenario_diag.py — "파일만으로 메뉴 트리를 얼마나 완성할 수 있는가" 진단.

시나리오 간 이동은 두 가지:
  - 정적 이동: <TargetPage> 속성에 대상 파일명이 박혀 있음 → 100% 자동 연결
  - 동적 이동: 대상이 <Script>/<PreScript> 안에서 계산됨 → 정적 파싱으로 못 잡음

이 스크립트는 동적 이동이 몇 %인지, 그 중 스크립트에서 파일명 리터럴로
복구 가능한 게 몇 %인지 측정한다. 이 수치가 트리 완성도의 상한을 결정한다.

사용:  python scenario_diag.py <시나리오폴더>
"""
import os
import re
import sys
import glob
import xml.etree.ElementTree as ET
from collections import defaultdict

NAV_TYPES = {"CallPageNode", "GotoPageNode"}

# 스크립트에서 페이지 대상으로 의심되는 패턴
_XML_LITERAL = re.compile(r'["\']([\w가-힣\-\.]+\.d?xml)["\']', re.I)
_PAGE_ASSIGN = re.compile(r'(app\.\w*(?:page|target|goto|next|move|jump)\w*)\s*=', re.I)


def _txt(node, tag):
    el = node.find(tag) if node is not None else None
    return el.text.strip() if (el is not None and el.text) else ""


def _stem(name):
    return os.path.splitext(os.path.basename(name))[0].lower()


def analyze(folder):
    files = sorted(glob.glob(os.path.join(folder, "*.dxml")) +
                   glob.glob(os.path.join(folder, "*.xml")))
    stems = {}
    for f in files:
        stems.setdefault(_stem(f), os.path.basename(f))

    nav_total = static_ok = static_broken = dynamic = 0
    dyn_recoverable = dyn_opaque = 0
    dynamic_nodes = []        # (file, seq, label, recovered_targets)
    page_assign_hits = 0
    referenced = set()        # 정적으로 참조되는 stem
    lit_referenced = set()    # 스크립트 리터럴로 등장하는 stem

    for f in files:
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        fname = os.path.basename(f)

        # 전체 스크립트에서 등장하는 .xml 리터럴 수집 (동적 호출 복구용)
        for node in root.findall("./Nodes/Node"):
            cp = node.find("CustomProperties")
            body = ((cp.findtext("Script") or "") + "\n" + (cp.findtext("PreScript") or "")) if cp is not None else ""
            for m in _XML_LITERAL.findall(body):
                if _stem(m) in stems:
                    lit_referenced.add(_stem(m))
            if _PAGE_ASSIGN.search(body):
                page_assign_hits += 1

        # 이동 노드 분류
        for node in root.findall("./Nodes/Node"):
            if node.get("NodeType") not in NAV_TYPES:
                continue
            nav_total += 1
            cp = node.find("CustomProperties")
            seq = _txt(cp, "Sequence")
            label = re.sub(r"\s+", " ", (node.findtext("Text") or "").strip())
            tp = _txt(cp, "TargetPage")
            if tp:
                if _stem(tp) in stems:
                    static_ok += 1
                    referenced.add(_stem(tp))
                else:
                    static_broken += 1
            else:
                dynamic += 1
                body = ((cp.findtext("Script") or "") + "\n" + (cp.findtext("PreScript") or "")) if cp is not None else ""
                found = [t for t in _XML_LITERAL.findall(body) if _stem(t) in stems]
                if found:
                    dyn_recoverable += 1
                    dynamic_nodes.append((fname, seq, label, found))
                else:
                    dyn_opaque += 1
                    dynamic_nodes.append((fname, seq, label, []))

    # 루트 = 정적으로 아무도 안 부르는 페이지
    static_roots = set(stems) - referenced
    # 그 중 스크립트 리터럴로는 불리는 것 (진짜 루트 아님 → 동적 복구 대상)
    roots_recoverable = static_roots & lit_referenced
    true_roots = static_roots - lit_referenced

    def pct(a, b):
        return (a / b * 100) if b else 0.0

    print(f"\n{'='*62}")
    print(f" 메뉴 트리 완성도 진단: {folder}")
    print(f"{'='*62}")
    print(f" 파일(시나리오)         : {len(files)}")
    print(f" 이동 노드(Call/Goto)   : {nav_total}")
    print(f"\n[이동 연결 분류]")
    print(f"  정적 연결 (자동)      : {static_ok:5}  ({pct(static_ok, nav_total):4.1f}%)")
    print(f"  정적이나 파일없음     : {static_broken:5}  ({pct(static_broken, nav_total):4.1f}%)")
    print(f"  동적 · 복구가능       : {dyn_recoverable:5}  ({pct(dyn_recoverable, nav_total):4.1f}%)  ← 스크립트에 .xml 리터럴 존재")
    print(f"  동적 · 불투명         : {dyn_opaque:5}  ({pct(dyn_opaque, nav_total):4.1f}%)  ← 대상 계산됨, 정적추출 불가")

    coverage = pct(static_ok + dyn_recoverable, nav_total)
    print(f"\n[트리 완성 가능 상한]")
    print(f"  자동 연결 상한        : {coverage:4.1f}%  (정적 + 복구가능)")
    print(f"  스크립트 페이지 변수 할당 감지: {page_assign_hits}곳")

    print(f"\n[루트(진입점) 판정]")
    print(f"  정적 루트             : {len(static_roots)}")
    print(f"  └ 실제 루트           : {len(true_roots)}  (스크립트에도 안 불림)")
    print(f"  └ 동적 호출됨(가짜)   : {len(roots_recoverable)}  ← 이만큼 트리에 편입 가능")

    if dyn_opaque:
        print(f"\n[불투명 동적 이동 샘플 (상위 15)]")
        opaque = [d for d in dynamic_nodes if not d[3]][:15]
        for fname, seq, label, _ in opaque:
            print(f"   {fname}  [{seq}] {label}")

    verdict = ("동적 비율 낮음 → 트리/핵심세부 바로 구현 가능" if coverage >= 92
               else "동적 비율 보통 → 리터럴 복구 로직 넣고 구현 권장" if coverage >= 75
               else "동적 비율 높음 → 스크립트 이동 추출 선행 필요")
    print(f"\n{'='*62}")
    print(f" 판정: 자동연결 {coverage:.1f}% → {verdict}")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    analyze(sys.argv[1] if len(sys.argv) > 1 else "scenario_cache/운영")
