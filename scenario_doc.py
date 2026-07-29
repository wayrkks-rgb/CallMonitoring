# -*- coding: utf-8 -*-
"""
scenario_doc.py — 현업 제공용 트리 구성도 문서 모델.

특징
  - 메뉴 계층 유지: 각 노드는 '자기 몫'의 처리만 표기 (상위 처리 반복 없음)
  - 내용 중심: 단계 칩/제목이 phase 이름이 아니라 실제 하는 일
  - 세부 단계: 번호로 나눈 substep + MCI 보내는/받는 정보
  - 로그 연계·버전비교용: 각 substep 에 blocks(Sequence) 와 hash 를 숨겨서 보관
"""
import os
import hashlib

from biz_lang import clean_label, describe_item, phase_desc

# 그룹 제목 생성 시 phase 별 접미
TITLE_SUFFIX = {
    "입력": "입력",
    "조회·확인": "확인",
    "인증": "확인",
    "동의": "동의",
    "안내": "안내",
    "처리": "처리",
    "완료": "완료",
}


def _h(*parts):
    m = hashlib.md5()
    for p in parts:
        m.update((p or "").encode("utf-8", "ignore"))
    return m.hexdigest()[:10]


def _group_title(phase, items):
    """그룹 제목을 '내용'으로 만든다. (예: '자격 검증 3건', '금리조회 · 대출등록')"""
    labels = [clean_label(i.get("label")) for i in items if i.get("label")]
    labels = [x for x in labels if x]
    if not labels:
        return phase
    if phase == "인증":
        methods = [i.get("auth_method") for i in items if i.get("auth_method")]
        if methods:
            uniq = list(dict.fromkeys(methods))
            return " · ".join(uniq) + " 확인"
    if phase == "처리":
        mcis = [i for i in items if i.get("mci")]
        if mcis:
            names = []
            for i in mcis[:3]:
                nm = clean_label(i.get("label"))
                names.append(nm.split()[0] if nm else "연동")
            return " · ".join(dict.fromkeys(names))
    if phase == "조회·확인" and len(labels) >= 3:
        return f"{labels[0]} 외 {len(labels) - 1}건 확인"
    if phase == "완료":
        return "처리 완료 안내"
    if phase == "처리" and all(("." in x or "-" in x) and len(x) < 22 for x in labels[:3]):
        return "하위 메뉴 선택"
    head = " · ".join(dict.fromkeys(labels[:2]))
    if len(labels) > 2:
        head += f" 외 {len(labels) - 2}건"
    suf = TITLE_SUFFIX.get(phase, "")
    if suf and not head.endswith(suf):
        head = f"{head} {suf}"
    return head


def _mci_lines(mci):
    """MCI 보내는/받는 정보 (한글 주석 기반)."""
    if not mci:
        return None
    ins = [c for _v, _val, c in (mci.get("inputs") or []) if c][:6]
    outs = []
    for _o, fld in (mci.get("outputs") or [])[:6]:
        outs.append(fld)
    return {"send": ins, "recv": outs, "kind": mci.get("kind", "연동")}


def build_node_doc(bizflow_summary):
    """bizflow 의 summary_steps → 문서용 그룹 목록."""
    groups = []
    for m in bizflow_summary or []:
        items = m.get("steps") or []
        subs = []
        for it in items:
            mci = it.get("_mci_raw")
            sub = {
                "text": it.get("desc") or clean_label(it.get("label")),
                "label": clean_label(it.get("label")),
                "blocks": it.get("blocks") or ([it["seq"]] if it.get("seq") else []),
                "hash": _h(it.get("label"), it.get("cond"), it.get("script_hash")),
            }
            io = _mci_lines(mci)
            if io:
                sub["io"] = io
            subs.append(sub)
        fails = []
        for r in (m.get("routes") or []):
            fails.append({
                "when": r.get("cond") or f"{clean_label(r.get('from'))} 조건 미충족",
                "to": clean_label(r.get("to")),
            })
        trivial = (m.get("phase") == "진입" and len(subs) <= 1)
        groups.append({
            "trivial": trivial,
            "phase": m.get("phase"), "color": m.get("color"),
            "title": _group_title(m.get("phase"), items),
            "desc": phase_desc(m.get("phase")),
            "substeps": subs,
            "fails": fails,
            "hash": _h(m.get("phase"), *[s["hash"] for s in subs]),
        })
    return groups


def file_version(folder, page):
    """문서 기준 정보 (버전 비교용)."""
    p = os.path.join(folder, page)
    if not os.path.isfile(p):
        return None
    st = os.stat(p)
    with open(p, "rb") as f:
        h = hashlib.md5(f.read()).hexdigest()[:12]
    return {"file": page, "mtime": int(st.st_mtime), "size": st.st_size, "hash": h}
