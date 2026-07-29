# -*- coding: utf-8 -*-
"""
scenario_detail.py — 상세 뷰 엔진 (블록 카드 + 변수 추적 + 연결정보).

- 블록 단위: Sequence(블록ID), 종류, 조건식, 스크립트, 이전/다음 연결
- 변수 추적: app.X 가 어느 블록에서 만들어지고(set) 어느 블록에서 쓰이는지(use)
- 변수 용어사전: 작성자 주석(`app.X = v; // 대출신청금액`)에서 한글 이름 수집 → 풀어쓰기에 사용
- 조건식 풀어쓰기: 현업용 문장 생성 (원문은 개발자 영역에 유지)
전부 XML 기반, LLM 없음.
"""
import os
import re
import glob
import xml.etree.ElementTree as ET

from scenario_parser import parse_page
from phase import classify

ASSIGN = re.compile(r'^(app\.\w+)\s*(?<![=!<>])=(?!=)\s*([^;\n]+?)\s*;?\s*(?://\s*(.+))?$')
USE = re.compile(r'app\.\w+')
NUMWRAP = re.compile(r'Number\(\s*(app\.\w+)\s*\)')

# 공통 ARS 변수 기본 사전 (주석에서 못 얻는 것 보완 · 메뉴 무관 공통)
BASE_GLOSSARY = {
    "app._InputValue": "고객 입력값",
    "app._InputDigitLength": "입력 자릿수",
    "app.CustID": "고객ID",
    "app.SocialID": "주민등록번호",
    "app.PoliceNo": "증권번호",
    "app.Cust_Phone": "고객 휴대폰번호",
    "app.PassReg": "비밀번호 등록여부",
    "app.PassCard": "보안카드 발급여부",
    "app.pwPass": "비밀번호 인증 통과여부",
    "app.Err_Code": "오류코드",
    "app.tMoney": "신청금액(만원)",
    "app.LoanApp_Money": "신청금액(원)",
    "app.LoanPay_Possible": "대출가능금액",
    "app.Agent_ID": "상담사ID",
    "app.nowServiceCode": "서비스코드",
}

ATOM_PATTERNS = [
    (re.compile(r'^(app\.\w+)\.substr\(\s*0\s*,\s*(\d+)\s*\)\s*(==|!=)\s*["\']([^"\']*)["\']$'),
     lambda m, g: (m.group(1), f"{g(m.group(1))}의 앞 {m.group(2)}자리가 '{m.group(4)}'" +
                   ("" if m.group(3) == "==" else "가 아님"))),
    (re.compile(r'^(app\.\w+)\.length\s*(==|!=|<|>|<=|>=)\s*(\d+)$'),
     lambda m, g: (m.group(1), f"{g(m.group(1))}의 길이가 {m.group(3)}" +
                   {"==": "", "!=": "이 아님", "<": " 미만", ">": " 초과",
                    "<=": " 이하", ">=": " 이상"}[m.group(2)])),
    (re.compile(r'^(\w+)\.(\w+)\(\s*(app\.\w+)\s*\)\s*==\s*["\']Y["\']$'),
     lambda m, g: (m.group(3), f"{g(m.group(3))} 형식이 올바른지")),
    (re.compile(r'^(app\.\w+)\s*\+\s*(app\.\w+)\s*(>|>=|<|<=)\s*(\d+)$'),
     lambda m, g: (m.group(1), f"{g(m.group(1))}과(와) {g(m.group(2))}의 합이 {int(m.group(4)):,}" +
                   {">": " 초과", ">=": " 이상", "<": " 미만", "<=": " 이하"}[m.group(3)])),
    (re.compile(r'^(app\.\w+)\+\+?\s*(>|>=)\s*(\d+)$'),
     lambda m, g: (m.group(1), f"{g(m.group(1))}이(가) {m.group(3)}회" +
                   (" 초과" if m.group(2) == ">" else " 이상"))),
    (re.compile(r'^(app\.\w+)\s*(==|!=)\s*["\']([^"\']*)["\']$'),
     lambda m, g: (m.group(1), (f"{g(m.group(1))}이(가) " +
                   (f"'{m.group(3)}'" if m.group(3) else "비어 있음") +
                   ("" if m.group(2) == "==" else "이 아님")))),
    (re.compile(r'^(app\.\w+)\s*(<|>|<=|>=)\s*["\']?(\d+)["\']?$'),
     lambda m, g: (m.group(1), f"{g(m.group(1))}이(가) {int(m.group(3)):,}" +
                   {"<": " 미만", ">": " 초과", "<=": " 이하", ">=": " 이상"}[m.group(2)])),
    (re.compile(r'^(app\.\w+)\s*(<|>|<=|>=|==|!=)\s*(app\.\w+)$'),
     lambda m, g: (m.group(1), f"{g(m.group(1))}이(가) {g(m.group(3))}" +
                   {"<": "보다 작음", ">": "보다 큼", "<=": " 이하", ">=": " 이상",
                    "==": "과(와) 같음", "!=": "과(와) 다름"}[m.group(2)])),
]


def _split_top(expr, op):
    """괄호 밖의 op 로 분리."""
    parts, depth, cur = [], 0, ""
    i = 0
    while i < len(expr):
        c = expr[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if depth == 0 and expr[i:i + len(op)] == op:
            parts.append(cur.strip())
            cur = ""
            i += len(op)
            continue
        cur += c
        i += 1
    parts.append(cur.strip())
    return [p for p in parts if p]


def _strip_paren(s):
    s = s.strip()
    while s.startswith("(") and s.endswith(")"):
        inner = s[1:-1]
        d = 0
        ok = True
        for c in inner:
            if c == "(":
                d += 1
            elif c == ")":
                d -= 1
            if d < 0:
                ok = False
                break
        if not ok or d != 0:
            break
        s = inner.strip()
    return s


def _atom(expr, g):
    """단일 비교식 → (변수, 서술). 실패 시 (None, None)."""
    e = _strip_paren(expr)
    for pat, fn in ATOM_PATTERNS:
        m = pat.match(e)
        if m:
            try:
                return fn(m, g)
            except Exception:
                return (None, None)
    if re.match(r'^app\.\w+$', e):
        return (e, f"{g(e)} 값")
    return (None, None)


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())



# 변수명 토큰 → 한글 (주석이 없는 변수를 자동 한글화)
TOKENS = [
    ("Possible", "가능금액"), ("Police", "증권"), ("Policy", "증권"),
    ("Account", "계좌"), ("Acc", "계좌"), ("Bank", "은행"), ("Loan", "대출"),
    ("Cust", "고객"), ("Social", "주민등록번호"), ("Phone", "전화번호"),
    ("Agent", "상담사"), ("Service", "서비스"), ("Svc", "서비스"),
    ("Error", "오류"), ("Err", "오류"), ("Result", "결과"), ("Rslt", "결과"),
    ("Password", "비밀번호"), ("Passwd", "비밀번호"), ("Pass", "비밀번호"),
    ("Card", "카드"), ("Menu", "메뉴"), ("Fast", "빠른"), ("Today", "당일"),
    ("Otpy", "OTP"), ("Otp", "OTP"), ("Reg", "등록"), ("Check", "확인"),
    ("Chk", "확인"), ("Limit", "한도"), ("Amount", "금액"), ("Amt", "금액"),
    ("Money", "금액"), ("Date", "일자"), ("Time", "시각"), ("Code", "코드"),
    ("Count", "횟수"), ("Cnt", "건수"), ("Flag", "여부"), ("Type", "구분"),
    ("Input", "입력"), ("Output", "결과"), ("Temp", "임시"), ("Max", "최대"),
    ("Min", "최소"), ("Sum", "합계"), ("Total", "합계"), ("Name", "명"),
    ("Num", "번호"), ("No", "번호"), ("Yn", "여부"), ("YN", "여부"),
    ("Pay", "납입"), ("Data", "데이터"), ("List", "목록"), ("Info", "정보"),
    ("Cnfm", "확인"), ("Rcv", "수신"), ("Snd", "발신"), ("Host", "호스트"),
    ("AG", "나이"), ("HP", "휴대폰"), ("ARS", "ARS"), ("Sms", "문자"), ("SMS", "문자"),
    ("Vip", "VIP"), ("Grad", "등급"), ("Cert", "인증"), ("Certify", "인증"),
    ("Short", "단축"), ("Silver", "실버"), ("Emp", "직원"), ("Dvsn", "구분"),
]
SUFFIX_TOKENS = {"여부", "구분", "건수", "횟수"}


def auto_korean(var):
    """주석이 없는 app.X 변수명을 토큰 기반으로 한글화. 실패 시 None."""
    raw = var.replace("app.", "").strip("_")
    if not raw or re.search(r"[가-힣]", raw):
        return None
    # 언더스코어/카멜 분해
    parts = []
    for chunk in raw.split("_"):
        found = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+", chunk)
        for fpt in found:
            if fpt.isupper() and len(fpt) > 3:      # ACCPASSYN 같은 연속 대문자
                rest, acc = fpt, []
                while rest:
                    for en, _ko in sorted(TOKENS, key=lambda x: -len(x[0])):
                        if rest.upper().startswith(en.upper()):
                            acc.append(rest[:len(en)])
                            rest = rest[len(en):]
                            break
                    else:
                        acc.append(rest)
                        rest = ""
                parts += acc
            else:
                parts.append(fpt)
    out, hit = [], 0
    for pt in parts:
        if pt.isdigit():
            continue
        found = None
        for en, ko in TOKENS:
            if pt.lower() == en.lower():
                found = ko
                break
        if found:
            out.append(found)
            hit += 1
        else:
            out.append(pt)
    if not hit:
        return None
    tail = [x for x in out if x in SUFFIX_TOKENS]
    head = [x for x in out if x not in SUFFIX_TOKENS and x.upper() != "ID"]
    parts2 = head + tail
    # 중복 제거(연속 동일어)
    dedup = []
    for x in parts2:
        if not dedup or dedup[-1] != x:
            dedup.append(x)
    name = " ".join(dedup).strip()
    return name if name else None


def build_glossary(folder):
    """모든 파일의 `app.X = v; // 주석` 에서 변수 한글 이름 수집 (기본 사전 포함)."""
    gl = dict(BASE_GLOSSARY)
    for f in glob.glob(os.path.join(folder, "*.xml")) + glob.glob(os.path.join(folder, "*.dxml")):
        try:
            root = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        for n in root.findall(".//Node"):
            cp = n.find("CustomProperties")
            if cp is None:
                continue
            body = (cp.findtext("Script") or "") + "\n" + (cp.findtext("PreScript") or "")
            for line in body.split("\n"):
                m = ASSIGN.search(line.strip())
                if m and m.group(3):
                    cmt = m.group(3).strip()
                    # 주석이 한글을 포함하고 너무 길지 않을 때만
                    if re.search(r"[가-힣]", cmt) and len(cmt) <= 24:
                        gl.setdefault(m.group(1), re.split(r"[(\[]", cmt)[0].strip())
    return gl


def _fin(desc):
    """서술 → 자연스러운 '~인지 확인' 어미."""
    d = desc.strip()
    d = d.replace("비어 있음이 아님", "비어 있지 않은지").replace("비어 있음", "비어 있는지")
    if d.endswith("올바른지") or d.endswith("는지") or d.endswith("은지"):
        return d + " 확인"
    return d + "인지 확인"


def _plain_cond(cond, glossary):
    """조건식 → 현업용 문장. OR/AND 분해 후 서술. 실패 시 None."""
    c = _clean(cond)
    if not c:
        return None
    c = NUMWRAP.sub(r"\1", c)
    c = _strip_paren(c)

    def g(var):
        v = glossary.get(var)
        if v:
            return v
        return auto_korean(var) or var.replace("app.", "")

    # 단일 변수 (Switch 분기)
    if re.match(r'^app\.\w+$', c):
        return f"{g(c)}에 따라 분기"
    if re.match(r'^app\.\w+\.substr\(\s*0\s*,\s*(\d+)\s*\)$', c):
        m = re.match(r'^(app\.\w+)\.substr\(\s*0\s*,\s*(\d+)\s*\)$', c)
        return f"{g(m.group(1))}의 앞 {m.group(2)}자리에 따라 분기"

    ors = _split_top(c, "||")
    if len(ors) > 1:
        vals, var0, descs = [], None, []
        for o in ors:
            v, d = _atom(o, g)
            if d is None:
                return None
            descs.append(d)
            m = re.search(r"'([^']*)'", d)
            if var0 is None:
                var0 = v
            if v == var0 and m:
                vals.append(m.group(1))
            else:
                vals = None if vals is None else (vals if v == var0 else None)
        # 같은 변수의 값 나열이면 압축
        if var0 and vals and len(vals) == len(ors):
            head = descs[0].split("이(가)")[0].split("의 앞")[0]
            joined = ", ".join(f"'{x}'" for x in vals)
            if "앞" in descs[0]:
                dg = re.search(r"앞 (\d+)자리", descs[0])
                n = dg.group(1) if dg else ""
                return f"{g(var0)}의 앞 {n}자리가 {joined} 중 하나인지 확인"
            return f"{g(var0)}이(가) {joined} 중 하나인지 확인"
        return _fin(" 또는 ".join(descs))

    ands = _split_top(c, "&&")
    if len(ands) > 1:
        descs = []
        for a in ands:
            v, d = _atom(a, g)
            if d is None:
                return None
            descs.append(d)
        # A >= n && A != 0 형태는 앞만
        if len(descs) == 2 and "비어 있음이 아님" in descs[1]:
            return _fin(descs[0])
        return _fin(" 그리고 ".join(descs))

    v, d = _atom(c, g)
    return _fin(d) if d else None



# 입력 설정 추출 (GetDigit 계열)
INPUT_CFG = {
    "_InputDigitLength": "최대 자릿수",
    "_InputMinDigitLength": "최소 자릿수",
    "_TermDigitMask": "종료키",
    "_InputDigitMask": "허용 입력",
    "_InputRetryCount": "재시도 횟수",
    "_InputTimeout": "입력 대기(ms)",
    "_InputAudioFile": "안내 멘트",
    "_InputAudioFileTimeout": "무입력 멘트",
    "_InputAudioFileInvalid": "오입력 멘트",
}


def _input_cfg(body):
    out = []
    for line in body.split("\n"):
        m = ASSIGN.search(line.strip())
        if not m:
            continue
        key = m.group(1).replace("app.", "")
        if key in INPUT_CFG:
            out.append((INPUT_CFG[key], m.group(2).strip().strip('"')))
    return out


def block_desc(n, cond_plain, sets, input_cfg, sub_target, glossary):
    """블록별 업무 설명."""
    from biz_lang import clean_label
    lbl = clean_label(n.get("label") or "") or n.get("type", "")
    t = n.get("type") or ""
    if t in ("IfNode", "SwitchNode"):
        return cond_plain or f"{lbl} 조건에 따라 흐름을 나눕니다."
    if t == "ServiceCheckNode":
        return f"{lbl}: 해당 서비스가 차단되었는지 확인해, 차단 시 안내 후 다른 경로로 보냅니다."
    if t == "GetDigitPromptNode":
        cfg = ", ".join(f"{k} {v}" for k, v in input_cfg[:3])
        return f"{lbl}을(를) 안내하고 고객의 번호 입력을 받습니다." + (f" ({cfg})" if cfg else "")
    if t == "PromptNode":
        return f"{lbl} 내용을 고객에게 음성으로 안내합니다."
    if t == "ScriptNode":
        if sets:
            names = ", ".join(glossary.get(x["var"], x["var"].replace("app.", "")) for x in sets[:4])
            return f"{names} 값을 설정합니다."
        return f"{lbl} 처리를 수행합니다."
    if t == "CallPageNode":
        return f"{lbl}: {sub_target or '하위 시나리오'}를 호출해 처리하고 결과를 받아 돌아옵니다."
    if t == "GotoPageNode":
        return f"{lbl}: {sub_target or '다른 시나리오'}로 이동합니다. (복귀하지 않음)"
    if t in ("ReturnPageNode",):
        return f"{lbl}: 호출한 상위 시나리오로 결과를 돌려주고 복귀합니다."
    if t in ("StopNode", "HangupNode", "StopSmartIVRNode"):
        return f"{lbl}: 통화를 종료합니다."
    if t in ("StartNode",):
        return "이 시나리오가 시작되는 지점입니다."
    if t == "MemoNode":
        return f"메모: {lbl}"
    return lbl


def build_detail(folder, entry, glossary=None):
    """단일 시나리오 → 블록 카드 목록 + 변수 인덱스."""
    path = os.path.join(folder, entry) if not os.path.isabs(entry) else entry
    if not os.path.isfile(path):
        return {"error": f"시나리오 없음: {entry}"}
    glossary = glossary if glossary is not None else build_glossary(folder)

    g = parse_page(path)
    by_id = {n["id"]: n for n in g["nodes"]}
    out_edges, in_edges = {}, {}
    for e in g["edges"]:
        out_edges.setdefault(e["from"], []).append(e)
        in_edges.setdefault(e["to"], []).append(e)

    # 원본 XML에서 스크립트/시퀀스 재수집
    root = ET.parse(path).getroot()
    raw = {}
    for n in root.findall(".//Node"):
        cp = n.find("CustomProperties")
        raw[n.get("Id")] = {
            "seq": (cp.findtext("Sequence") if cp is not None else "") or "",
            "script": (cp.findtext("Script") if cp is not None else "") or "",
            "prescript": (cp.findtext("PreScript") if cp is not None else "") or "",
        }

    seq_of = {nid: raw.get(nid, {}).get("seq", "") for nid in by_id}
    blocks, var_set, var_use = [], {}, {}

    for nid, n in by_id.items():
        r = raw.get(nid, {})
        body = (r.get("prescript", "") + "\n" + r.get("script", "")).strip()
        seq = r.get("seq", "")

        sets, uses = [], set()
        for line in body.split("\n"):
            s = line.strip()
            if not s or s.startswith("//"):
                continue
            m = ASSIGN.search(s)
            if m:
                var, val, cmt = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
                sets.append({"var": var, "value": val, "comment": cmt})
                var_set.setdefault(var, []).append(seq)
                for u in USE.findall(val):
                    if u != var:
                        uses.add(u)
            else:
                for u in USE.findall(s):
                    uses.add(u)
        cond = n.get("condition", "")
        for u in USE.findall(cond):
            uses.add(u)
        for u in uses:
            var_use.setdefault(u, []).append(seq)

        icfg = _input_cfg(body)
        cplain = _plain_cond(cond, glossary) if cond else None
        ph, color, method, util = classify(n.get("target_page") or n["label"])
        blocks.append({
            "desc": block_desc(n, cplain, sets, icfg, n.get("target_page"), glossary),
            "input_cfg": icfg,
            "seq": seq, "nid": nid, "label": n["label"] or n["type"], "type": n["type"],
            "kind": n["kind"], "phase": ph, "phase_color": color, "auth_method": method,
            "util": util,
            "cond": cond, "cond_plain": cplain,
            "script": body,
            "sub_target": n.get("target_page", ""),
            "sets": sets, "uses": sorted(uses),
            "next": [{"label": e["label"], "seq": seq_of.get(e["to"], ""),
                      "to": (by_id[e["to"]]["label"] if e["to"] in by_id else "?")}
                     for e in out_edges.get(nid, [])],
            "prev": [{"label": e["label"], "seq": seq_of.get(e["from"], ""),
                      "from": (by_id[e["from"]]["label"] if e["from"] in by_id else "?")}
                     for e in in_edges.get(nid, [])],
        })

    # 흐름 순서로 정렬 (시작 노드부터 BFS)
    order, seen = [], set()
    start = next((n for n in g["nodes"] if n["type"] == "StartNode"), None)
    stack = [start["id"]] if start else []
    while stack:
        cid = stack.pop(0)
        if cid in seen or cid not in by_id:
            continue
        seen.add(cid)
        order.append(cid)
        for e in out_edges.get(cid, []):
            if e["to"] not in seen:
                stack.append(e["to"])
    rank = {nid: i for i, nid in enumerate(order)}
    blocks.sort(key=lambda b: rank.get(b["nid"], 9999))

    # 변수 인덱스 (블록ID 기준)
    variables = {}
    for v in set(list(var_set) + list(var_use)):
        variables[v] = {
            "name": glossary.get(v, ""),
            "set_at": sorted(set(var_set.get(v, []))),
            "used_at": sorted(set(var_use.get(v, []))),
        }

    return {"page": os.path.basename(path), "blocks": blocks, "variables": variables}
